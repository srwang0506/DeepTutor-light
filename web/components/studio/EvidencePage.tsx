'use client'
import { useTranslation } from 'react-i18next'
import { studioLabel } from './display'
import { getLocale } from '@/lib/datetime'
import ReviewProtocols from './ReviewProtocols'
import ClaimsPanel from './ClaimsPanel'
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { apiFetch } from '@/lib/api'
import { ArrowUpRight, Check, Download, GitCompareArrows, Plus, Table2 } from 'lucide-react'
import { useActiveWorkspaceId } from '@/hooks/useActiveWorkspaceId'
import { scopedUrl } from '@/lib/workspace-scope'
import { Dialog } from '@/shared/ui/Dialog'
import {
  records,
  post,
  studio,
  studioUrl,
  emptyProtocol,
  PROTOCOL_FIELDS,
  type Protocol,
  type StudioRecord,
  type EvidenceData,
  type ObservationData,
  type RunData,
  type ArtifactData,
} from '@/lib/studio-api'
import { Action, ErrorNotice, Field, StudioEmpty, StudioShell, useOperation } from './StudioShell'


interface Comparison {
  status: string
  unknown: string[]
  differences: { field: string; left: string; right: string }[]
  matched: string[]
  delta: number | null
  delta_unit?: string
  conversions?: { field: string; left: string; right: string }[]
}
const blank = (): ObservationData => ({
  title: '',
  origin: 'reported',
  source_id: '',
  protocol: emptyProtocol(),
  value: null,
  state: 'reported',
  note: '',
})
export default function EvidencePage() {
  const workspace = useActiveWorkspaceId()
  return <EvidenceWorkspace key={workspace} workspace={workspace} />
}
function EvidenceWorkspace({ workspace }: { workspace: string }) {
  const { t, i18n } = useTranslation()

  const states: Record<string, string> = {
  reported: t('有明确数值'),
  no_full_text: t('未取得全文'),
  not_found: t('尚未找到'),
  not_reported: t('原文未报告'),
  not_applicable: t('不适用'),
  conflict: t('来源冲突'),
}
  const origins: Record<string, string> = {
  reported: t('论文报告'),
  executed: t('实际运行'),
  static_check: t('静态检查'),
  hypothesis: t('研究假设'),
}

  const op = useOperation(),
    [tab, setTab] = useState('matrix'),
    [evidence, setEvidence] = useState<StudioRecord<EvidenceData>[]>([]),
    [observations, setObservations] = useState<StudioRecord<ObservationData>[]>([]),
    [runs, setRuns] = useState<StudioRecord<RunData>[]>([]),
    [artifacts, setArtifacts] = useState<StudioRecord<ArtifactData>[]>([])
  const [dialog, setDialog] = useState(false),
    [editing, setEditing] = useState<StudioRecord<ObservationData> | null>(null),
    [draft, setDraft] = useState(blank),
    [selected, setSelected] = useState<string[]>([]),
    [comparison, setComparison] = useState<Comparison | null>(null),
    [generated, setGenerated] = useState(false),
    [reviewing, setReviewing] = useState<StudioRecord<EvidenceData> | null>(null),
    [reviewNote, setReviewNote] = useState(''),
    [visualReview, setVisualReview] = useState(false)
  async function reload(signal?: AbortSignal) {
    const [a, b, c, d] = await Promise.all([
      records<EvidenceData>(workspace, 'evidence', signal),
      records<ObservationData>(workspace, 'observation', signal),
      records<RunData>(workspace, 'run', signal),
      records<ArtifactData>(workspace, 'artifact', signal),
    ])
    setEvidence(a)
    setObservations(b)
    setRuns(c)
    setArtifacts(d)
  }
  useEffect(() => {
    const abort = new AbortController()
    Promise.all([
      records<EvidenceData>(workspace, 'evidence', abort.signal),
      records<ObservationData>(workspace, 'observation', abort.signal),
      records<RunData>(workspace, 'run', abort.signal),
      records<ArtifactData>(workspace, 'artifact', abort.signal),
    ])
      .then(([a, b, c, d]) => {
        setEvidence(a)
        setObservations(b)
        setRuns(c)
        setArtifacts(d)
      })
      .catch((e) => {
        if (!abort.signal.aborted) op.setError(e.message)
      })
    return () => abort.abort()
  }, [workspace]) // eslint-disable-line react-hooks/exhaustive-deps
  async function downloadArtifact(id: string, name: string) {
    const response = await apiFetch(
      studioUrl(`/artifacts/${id}/files/${encodeURIComponent(name)}`, workspace),
    )
    if (!response.ok) throw new Error(t('暂时无法读取图表文件。'))
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = name
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  function edit(row?: StudioRecord<ObservationData>) {
    setEditing(row || null)
    const next = row ? structuredClone(row.data) : blank()
    delete next.source_ref
    setDraft(next)
    setDialog(true)
  }
  async function save() {
    if (editing)
      await studio(workspace, `/observations/${editing.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...draft, expected_revision: editing.revision }),
      })
    else await post(workspace, '/observations', draft)
    setDialog(false)
    setComparison(null)
    await reload()
  }
  async function compare() {
    setComparison(
      await studio<Comparison>(workspace, `/compare?left=${selected[0]}&right=${selected[1]}`),
    )
  }
  async function generate(kind: string) {
    await post(workspace, '/artifacts', {
      title: `${kind === 'table' ? t('证据比较表') : t('结果比较图')} · ${new Date().toLocaleDateString(getLocale(i18n.resolvedLanguage || i18n.language))}`,
      kind,
      observation_ids: selected,
    })
    setGenerated(true)
    await reload()
  }
  async function review(row: StudioRecord<EvidenceData>, verification: string) {
    await studio(workspace, `/evidence/${row.id}/review`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        expected_revision: row.revision,
        verification,
        visual_review: visualReview ? 'confirmed' : 'not_reviewed',
        review_note: reviewNote,
      }),
    })
    await reload()
    setComparison(null)
  }
  const label = (field: string) =>
    t(PROTOCOL_FIELDS.find(([key]) => key === field)?.[1] ||
    {
      direction: t('指标方向'),
      source_revision: t('来源已变化'),
      result_provenance: t('结果来源不足'),
      unreviewed_evidence: t('原文证据未核实'),
    }[field] ||
    studioLabel(t, field))
  return (
    <StudioShell
      workspace={workspace}
      active="evidence"
      title={t('让每个结论，都找得到来处。')}
      description={t("把原文、评估条件和实验结果放在一起。先核对可比性，再讨论差异。")}
      action={
        <Action onClick={() => edit()}>
          <Plus size={15} />
          {t('记录一个结果')}
        </Action>
      }
    >
      <ReviewProtocols workspace={workspace} />
      <ErrorNotice message={op.error} />
      <div className="studio-toolbar">
        <div className="studio-pills">
          {[
            ['matrix', t('结果矩阵'), observations.length],
            ['evidence', t('原文证据'), evidence.length],
            ['artifacts', t('表与图'), artifacts.length],
            ['claims', t('主张与反证'), ''],
          ].map(([key, title, count]) => (
            <button key={key} aria-pressed={tab === key} onClick={() => setTab(String(key))}>
              {title} <span className="studio-muted">{count}</span>
            </button>
          ))}
        </div>
        {tab === 'matrix' && (
          <div className="studio-actions">
            <Action
              secondary
              onClick={() => op.run(compare)}
              disabled={op.busy || selected.length !== 2}
            >
              <GitCompareArrows size={14} />
              {t('比较所选')}
            </Action>
            <Action
              secondary
              onClick={() => op.run(() => generate('table'))}
              disabled={op.busy || !selected.length}
            >
              <Table2 size={14} />
              {t('生成表格')}
            </Action>
            <Action
              secondary
              onClick={() => op.run(() => generate('figure'))}
              disabled={op.busy || selected.length < 2}
            >
              {t('生成图')}
            </Action>
          </div>
        )}
      </div>
      {tab === 'claims' && <ClaimsPanel workspace={workspace} />}
      {tab === 'matrix' && (
        <>
          {observations.length ? (
            <div className="studio-table-wrap">
              <table className="studio-table">
                <thead>
                  <tr>
                    <th style={{ width: 40 }}>{t('选择')}</th>
                    <th>{t('方法 / 结果')}</th>
                    <th>{t('评估条件')}</th>
                    <th>{t('数值')}</th>
                    <th>{t('来源')}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {observations.map((row) => (
                    <tr key={row.id}>
                      <td>
                        <input
                          type="checkbox"
                          aria-label={t("选择 {{value1}}", { value1: row.data.title })}
                          checked={selected.includes(row.id)}
                          onChange={(e) => {
                            setSelected((s) =>
                              e.target.checked ? [...s, row.id] : s.filter((id) => id !== row.id),
                            )
                            setComparison(null)
                          }}
                        />
                      </td>
                      <td>
                        <strong>{row.data.title}</strong>
                        <small>{row.data.protocol.metric || t('指标待补充')}</small>
                      </td>
                      <td>
                        {row.data.protocol.dataset || t('数据集未知')}
                        <small>
                          {row.data.protocol.split || t('划分未知')} ·{' '}
                          {row.data.protocol.input || t('输入条件未知')}
                        </small>
                      </td>
                      <td style={{ fontVariantNumeric: 'tabular-nums' }}>
                        {row.data.value ?? '—'} {row.data.protocol.unit}
                        <small>{states[row.data.state]}</small>
                      </td>
                      <td>
                        <span className="studio-badge">{origins[row.data.origin]}</span>
                        <small>
                          {evidence.find((e) => e.id === row.data.source_id)?.data.label ||
                            runs.find((r) => r.id === row.data.source_id)?.data.title ||
                            t('来源记录')}
                        </small>
                      </td>
                      <td>
                        <button className="studio-link-button" onClick={() => edit(row)}>
                          {t('核对 / 编辑')}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <StudioEmpty title={t('从一个有来源的结果开始')}>
              {t(
                '在资料库打开论文，选择原文并保存证据；也可以从已完成的实验记录提取结果。评估条件缺失时，系统会保留未知。',
              )}
            </StudioEmpty>
          )}
          {comparison && (
            <section className="studio-panel" style={{ marginTop: 22 }}>
              <h2>
                {comparison.status === 'comparable'
                  ? t('所选结果满足记录中的比较条件')
                  : comparison.status === 'incomparable'
                    ? t('评估条件存在差异')
                    : comparison.status === 'conflict'
                      ? t('来源之间存在冲突')
                      : t('还有条件需要核对')}
              </h2>
              <p>
                {comparison.status === 'comparable'
                  ? t("第二项相对第一项的数值差为 {{value1}} {{value2}}。此差值不代表统计显著性或因果关系。", { value1: comparison.delta, value2: comparison.delta_unit || '' })
                  : t('在条件核实前，不生成优劣排名或数值提升结论。')}
              </p>
              {comparison.conversions?.map((item) => (
                <p className="studio-note" key={item.field}>
                  {t('已按确定的单位关系换算')}
                  {label(item.field)}：{item.right} → {item.left}
                  {t('。原始值仍保留。')}
                </p>
              ))}
              {comparison.unknown.length > 0 && (
                <div className="studio-badges">
                  {comparison.unknown.map((field, i) => (
                    <span className="studio-badge warn" key={field + i}>
                      {label(field)}
                      {t('待核对')}
                    </span>
                  ))}
                </div>
              )}
              {comparison.differences.length > 0 && (
                <div className="studio-table-wrap" style={{ marginTop: 15 }}>
                  <table className="studio-table">
                    <thead>
                      <tr>
                        <th>{t('条件')}</th>
                        <th>{t('第一项')}</th>
                        <th>{t('第二项')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {comparison.differences.map((d) => (
                        <tr key={d.field}>
                          <td>{label(d.field)}</td>
                          <td>{['profile', 'direction'].includes(d.field) ? studioLabel(t, d.left) : d.left}</td>
                          <td>{['profile', 'direction'].includes(d.field) ? studioLabel(t, d.right) : d.right}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <p className="studio-note">
                {t('已对齐')}
                {comparison.matched.length}{' '}
                {t('项条件。原始协议和来源版本保存在每次生成的表图中。')}
              </p>
            </section>
          )}
          {generated && (
            <div className="studio-success" style={{ marginTop: 20 }}>
              <Check size={15} />
              {t('已生成并冻结输入版本。')}
              <button className="studio-link-button" onClick={() => setTab('artifacts')}>
                {t('查看表与图')}
                <ArrowUpRight size={12} />
              </button>
            </div>
          )}
        </>
      )}
      {tab === 'evidence' && (
        <div className="studio-card-list">
          {evidence.map((row) => (
            <article className="studio-card" key={row.id}>
              <div className="studio-actions">
                <span
                  className={`studio-badge ${row.data.verification === 'confirmed' ? 'good' : 'warn'}`}
                >
                  {row.data.verification === 'confirmed'
                    ? t('已核对原文')
                    : row.data.verification === 'disputed'
                      ? t('存在争议')
                      : t('待核对')}
                </span>
                <span className="studio-badge">
                  {t('第 {{page}} 页', { page: row.data.page_label || row.data.page })} ·{' '}
                  {studioLabel(t, row.data.kind)}
                </span>
              </div>
              <div className="studio-badges" style={{ marginTop: 8 }}>
                <span className="studio-badge">
                  {t('定位：')}
                  {row.data.anchor_valid ? t('有效') : t('待重核')}
                </span>
                <span
                  className={`studio-badge ${row.data.quote_match === 'exact' ? 'good' : 'warn'}`}
                >
                  {t('引句：')}
                  {(
                    {
                      exact: t('精确匹配'),
                      approximate: t('近似匹配'),
                      unmatched: t('未匹配'),
                      no_text: t('无文字层'),
                      not_requested: t('未摘录文字'),
                      ambiguous: t('多处匹配'),
                    } as Record<string, string>
                  )[row.data.quote_match || ''] || t('待核对')}
                </span>
                <span className="studio-badge">{t('主张支持需逐项核对')}</span>
              </div>
              {row.data.needs_review && (
                <p className="studio-note">{t('旧版核对记录已保留；请按当前规则重新核验。')}</p>
              )}
              <h3 style={{ marginTop: 12 }}>{row.data.label}</h3>
              {row.data.quote && <blockquote>{row.data.quote}</blockquote>}
              <p>{row.data.interpretation || t('尚未添加自己的解读。')}</p>
              <footer>
                <Link
                  className="studio-outlink"
                  href={scopedUrl(
                    `/library/${row.data.edition_id}?file=${row.data.blob_hash}&page=${row.data.page}&evidence=${row.id}`,
                    workspace,
                  )}
                >
                  {t('回到原文')}
                  <ArrowUpRight size={13} />
                </Link>
                <div className="studio-actions">
                  <button
                    className="studio-link-button"
                    disabled={op.busy}
                    onClick={() => {
                      setReviewing(row)
                      setReviewNote(row.data.review_note || '')
                      setVisualReview(false)
                    }}
                  >
                    {t('我已核实')}
                  </button>
                  <button
                    className="studio-link-button"
                    disabled={op.busy}
                    onClick={() => op.run(() => review(row, 'disputed'))}
                  >
                    {t('标记争议')}
                  </button>
                </div>
              </footer>
            </article>
          ))}
          {!evidence.length && (
            <StudioEmpty title={t('把阅读中最重要的片段留下来')}>
              {t('在 PDF 原文中选取文字，记录它支持什么、限制什么。每条证据都会绑定原文件与页码。')}
            </StudioEmpty>
          )}
        </div>
      )}
      {tab === 'artifacts' && (
        <div className="studio-card-list">
          {artifacts.map((row) => (
            <article className="studio-card" key={row.id}>
              <h3>{row.data.title}</h3>
              <p>
                {row.data.type === 'figure'
                  ? t('可重建的绘图产物，附输入、脚本与样式。')
                  : t('固定输入版本的比较表。未知条件单独保留，不自动排名。')}
              </p>
              <div className="studio-actions" style={{ marginTop: 16 }}>
                {Object.keys(row.data.files).map((name) => (
                  <button
                    className="studio-link-button"
                    key={name}
                    disabled={op.busy}
                    onClick={() => op.run(() => downloadArtifact(row.id, name))}
                  >
                    <Download size={12} /> {name}
                  </button>
                ))}
              </div>
            </article>
          ))}
          {!artifacts.length && (
            <StudioEmpty title={t('从核实后的结果，生成可复查的表与图')}>
              {t(
                '选择矩阵中的结果，生成 CSV、TeX 表格或 PDF、SVG、PNG 图。输入和生成脚本会随产物保存。',
              )}
            </StudioEmpty>
          )}
        </div>
      )}
      <Dialog
        open={dialog}
        onClose={() => !op.busy && setDialog(false)}
        title={editing ? t('核对结果与评估条件') : t('记录有来源的结果')}
        description={t("空白代表未知。只有确实不适用的条件才勾选“不适用”。")}
        size="lg"
        className="studio-dialog"
        footer={
          <Action
            disabled={op.busy || !draft.title.trim() || !draft.source_id}
            onClick={() => op.run(save)}
          >
            {t('保存结果')}
          </Action>
        }
      >
        <div className="studio-form">
          <ErrorNotice message={op.error} />
          <Field label={t("方法或结果名称")}>
            <input
              value={draft.title}
              onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))}
            />
          </Field>
          <div className="studio-form-grid">
            <Field label={t("结果性质")}>
              <select
                value={draft.origin}
                onChange={(e) => setDraft((d) => ({ ...d, origin: e.target.value, source_id: '' }))}
              >
                {Object.entries(origins).map(([key, title]) => (
                  <option value={key} key={key}>
                    {title}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t("来源记录")}>
              <select
                value={draft.source_id}
                onChange={(e) => setDraft((d) => ({ ...d, source_id: e.target.value }))}
              >
                <option value="">{t('选择来源')}</option>
                {draft.origin === 'reported'
                  ? evidence.map((e) => (
                      <option key={e.id} value={e.id}>
                        {e.data.label}
                      </option>
                    ))
                  : runs
                      .filter((r) =>
                        draft.origin === 'executed'
                          ? r.data.status === 'succeeded'
                          : draft.origin === 'static_check'
                            ? r.data.status === 'static_only'
                            : true,
                      )
                      .map((r) => (
                        <option key={r.id} value={r.id}>
                          {r.data.title}
                        </option>
                      ))}
              </select>
            </Field>
            <Field label={t("报告状态")}>
              <select
                value={draft.state}
                onChange={(e) =>
                  setDraft((d) => ({
                    ...d,
                    state: e.target.value,
                    value: e.target.value === 'reported' ? d.value : null,
                  }))
                }
              >
                {Object.entries(states).map(([key, title]) => (
                  <option key={key} value={key}>
                    {title}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t("数值")}>
              <input
                type="number"
                step="any"
                disabled={draft.state !== 'reported'}
                value={draft.value ?? ''}
                onChange={(e) =>
                  setDraft((d) => ({
                    ...d,
                    value: e.target.value === '' ? null : Number(e.target.value),
                  }))
                }
              />
            </Field>
          </div>
          <h3 className="studio-section-title" style={{ margin: 0 }}>
            {t('评估协议')}
          </h3>
          <Field label={t("任务口径")}>
            <select
              value={draft.protocol.profile || 'general'}
              onChange={(e) =>
                setDraft((d) => ({ ...d, protocol: { ...d.protocol, profile: e.target.value } }))
              }
            >
              {[
                ['general', t('通用定量比较')],
                ['hoi_detection', t('HOI 交互检测')],
                ['hoi_reconstruction', t('HOI 三维重建')],
                ['trajectory', t('轨迹评估')],
                ['mathematics', t('数学与数值方法')],
              ].map(([id, label]) => (
                <option key={id} value={id}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
          <p className="studio-note">
            {t('必要条件不能用“不适用”代替。数值差仅用于描述，不代表统计显著性。')}
          </p>
          <div className="studio-form-grid">
            {(
              (
                {
                  hoi_reconstruction: [
                    'target',
                    'template_dependency',
                    'calibration',
                    'coordinate_space',
                  ],
                  trajectory: [
                    'trajectory_source',
                    'coordinate_space',
                    'scale_normalization',
                    'evaluation_version',
                  ],
                  mathematics: ['assumptions', 'domain'],
                } as Record<string, string[]>
              )[draft.protocol.profile || ''] || []
            ).map((key) => (
              <Field key={key} label={studioLabel(t, key)}>
                <input
                  value={draft.protocol.extensions?.[key] || ''}
                  onChange={(e) =>
                    setDraft((d) => ({
                      ...d,
                      protocol: {
                        ...d.protocol,
                        extensions: { ...d.protocol.extensions, [key]: e.target.value },
                      },
                    }))
                  }
                />
              </Field>
            ))}
            {PROTOCOL_FIELDS.map(([key, title]) => (
              <div key={key}>
                <Field label={t(title)}>
                  <input
                    disabled={draft.protocol.not_applicable.includes(key)}
                    value={draft.protocol[key]}
                    onChange={(e) =>
                      setDraft((d) => ({
                        ...d,
                        protocol: {
                          ...d.protocol,
                          [key]: e.target.value,
                          field_states: {
                            ...d.protocol.field_states,
                            [key]:
                              !e.target.value.trim() ||
                              ['unknown', 'n/a', '?', '未知'].includes(
                                e.target.value.trim().toLowerCase(),
                              )
                                ? 'unknown'
                                : 'known',
                          },
                        },
                      }))
                    }
                    placeholder={t('尚未确认时留空')}
                  />
                </Field>
                <label className="studio-check">
                  <input
                    type="checkbox"
                    checked={draft.protocol.not_applicable.includes(key)}
                    onChange={(e) =>
                      setDraft((d) => ({
                        ...d,
                        protocol: {
                          ...d.protocol,
                          [key]: e.target.checked ? '' : d.protocol[key],
                          field_states: {
                            ...d.protocol.field_states,
                            [key]: e.target.checked ? 'not_applicable' : 'unknown',
                          },
                          not_applicable: e.target.checked
                            ? [...d.protocol.not_applicable, key]
                            : d.protocol.not_applicable.filter((k) => k !== key),
                        },
                      }))
                    }
                  />
                  {t('不适用')}
                </label>
              </div>
            ))}
          </div>
          <Field label={t("指标方向")}>
            <select
              value={draft.protocol.direction}
              onChange={(e) =>
                setDraft((d) => ({
                  ...d,
                  protocol: { ...d.protocol, direction: e.target.value as Protocol['direction'] },
                }))
              }
            >
              <option value="unknown">{t('尚未确认')}</option>
              <option value="higher">{t('越高越好')}</option>
              <option value="lower">{t('越低越好')}</option>
            </select>
          </Field>
          <Field label={t("核对备注")}>
            <textarea
              value={draft.note}
              onChange={(e) => setDraft((d) => ({ ...d, note: e.target.value }))}
            />
          </Field>
        </div>
      </Dialog>
      <Dialog
        open={!!reviewing}
        onClose={() => setReviewing(null)}
        title={t('核对证据原件')}
        description={t("核对的是原文或原图；是否支持某个结论，需要在主张中单独审核。")}
        className="studio-dialog"
        footer={
          <Action
            disabled={op.busy}
            onClick={() =>
              op.run(async () => {
                if (reviewing) await review(reviewing, 'confirmed')
                setReviewing(null)
              })
            }
          >
            {t('确认核对')}
          </Action>
        }
      >
        {reviewing && (
          <Link
            className="studio-outlink"
            target="_blank"
            href={scopedUrl(
              `/library/${reviewing.data.edition_id}?file=${reviewing.data.blob_hash}&page=${reviewing.data.page}&evidence=${reviewing.id}`,
              workspace,
            )}
          >
            {t('打开冻结原件 · 第')}
            {reviewing.data.page} {t('页 ↗')}
          </Link>
        )}
        <p className="studio-note">
          {t(
            '引句必须在所选区域匹配。扫描页、图表或公式没有文字层时，需要对照原图核验并留下说明。',
          )}
        </p>
        <Field label={t("核对说明")}>
          <textarea
            value={reviewNote}
            onChange={(e) => setReviewNote(e.target.value)}
            placeholder={t('核对了哪张图、哪一行或哪个公式；有哪些限制？')}
          />
        </Field>
        <label className="studio-check">
          <input
            type="checkbox"
            checked={visualReview}
            onChange={(e) => setVisualReview(e.target.checked)}
          />
          {t('我已对照原图完成视觉核验')}
        </label>
        <ErrorNotice message={op.error} />
      </Dialog>
    </StudioShell>
  )
}
