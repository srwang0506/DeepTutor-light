'use client'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Link from 'next/link'
import { Dialog } from '@/shared/ui/Dialog'
import { scopedUrl } from '@/lib/workspace-scope'
import {
  records,
  studio,
  post,
  studioUrl,
  type StudioRecord,
  type Work,
  type EvidenceData,
  type Edition,
} from '@/lib/studio-api'
import { Action, Field, ErrorNotice, useOperation } from './StudioShell'
interface Protocol {
  title: string
  question: string
  search_query: string
  inclusion: string[]
  exclusion: string[]
  extraction_fields: string[]
  edition_ids: string[]
}
interface Decision {
  decision: string
  reason: string
  edition_revision: number
}
interface Extraction {
  value: string
  evidence_ids: string[]
}
interface Item {
  title: string
  edition: Edition
  state: string
  stale: boolean
  decisions: Record<string, StudioRecord<Decision> | null>
  extractions: Record<string, (StudioRecord<Extraction> & { stale?: boolean }) | null>
}
interface Report {
  protocol: StudioRecord<Protocol>
  counts: Record<string, number>
  items: Item[]
}
export default function ReviewProtocols({ workspace }: { workspace: string }) {
  const { t } = useTranslation(),
    op = useOperation()
  const { setError } = op
  const [protocols, setProtocols] = useState<StudioRecord<Protocol>[]>([]),
    [report, setReport] = useState<Report | null>(null),
    [create, setCreate] = useState(false)
  const [works, setWorks] = useState<Work[]>([]),
    [cursor, setCursor] = useState(''),
    [selected, setSelected] = useState<string[]>([])
  const [draft, setDraft] = useState({
    title: '',
    question: '',
    search_query: '',
    inclusion: '',
    exclusion: '',
    extraction_fields: '',
  })
  const [item, setItem] = useState<Item | null>(null),
    [mode, setMode] = useState<'decision' | 'extraction'>('decision'),
    [stage, setStage] = useState('abstract'),
    [decision, setDecision] = useState('uncertain'),
    [reason, setReason] = useState(''),
    [reviewed, setReviewed] = useState(false)
  const [field, setField] = useState(''),
    [value, setValue] = useState(''),
    [evidence, setEvidence] = useState<StudioRecord<EvidenceData>[]>([]),
    [anchors, setAnchors] = useState<string[]>([])
  useEffect(() => {
    let active = true
    records<Protocol>(workspace, 'review_protocol')
      .then((r) => {
        if (active) setProtocols(r)
      })
      .catch((e) => {
        if (active) setError(String(e))
      })
    return () => {
      active = false
    }
  }, [workspace, setError])
  async function load(id: string) {
    setReport(await studio<Report>(workspace, `/reviews/protocols/${id}`))
  }
  const labels: Record<string, string> = {
    identified: t('候选'),
    abstract_pending: t('待题录筛选'),
    abstract_excluded: t('题录排除'),
    full_text_pending: t('待全文筛选'),
    full_text_excluded: t('全文排除'),
    included: t('纳入'),
    uncertain: t('待定'),
  }
  async function loadPapers(after = '') {
    const data = await studio<{ works: Work[]; next_cursor: string }>(
      workspace,
      `/library?limit=200&cursor=${encodeURIComponent(after)}`,
    )
    setWorks((old) => (after ? [...old, ...data.works] : data.works))
    setCursor(data.next_cursor)
  }
  return (
    <section className="studio-card">
      <div className="studio-toolbar">
        <div>
          <h3>{t('文献综述与证据提取')}</h3>
          <p className="studio-muted">
            {t('先冻结问题与标准，再逐篇筛选。所有纳排理由和提取结果都保留来源版本。')}
          </p>
        </div>
        <Action
          secondary
          onClick={() =>
            void op.run(async () => {
              await loadPapers()
              setCreate(true)
            })
          }
        >
          {t('建立综述')}
        </Action>
      </div>
      <ErrorNotice message={op.error} />
      <div className="studio-pills">
        {protocols.map((p) => (
          <button
            key={p.id}
            aria-pressed={report?.protocol.id === p.id}
            onClick={() => void op.run(() => load(p.id))}
          >
            {p.data.title}
          </button>
        ))}
      </div>
      {report && (
        <>
          <h4>{report.protocol.data.question}</h4>
          <p className="studio-muted">
            {t('冻结检索式')}：{report.protocol.data.search_query}
          </p>
          <details>
            <summary>{t('纳排标准与提取字段')}</summary>
            <p>
              {t('纳入')}：{report.protocol.data.inclusion.join('；')}
            </p>
            <p>
              {t('排除')}：{report.protocol.data.exclusion.join('；')}
            </p>
            <p>{report.protocol.data.extraction_fields.join(' · ')}</p>
          </details>
          <div className="studio-badges">
            {Object.entries(report.counts).map(([key, count]) => (
              <span key={key} className="studio-badge">
                {labels[key]} {count}
              </span>
            ))}
          </div>
          <a
            className="studio-link-button"
            href={studioUrl(`/reviews/protocols/${report.protocol.id}/export`, workspace)}
          >
            {t('导出提取表 CSV')}
          </a>
          {report.items.map((row) => (
            <article className="studio-card" key={row.edition.id}>
              <strong>{row.title}</strong>
              <p className="studio-muted">
                {labels[row.state]} · {t('版本')} {row.edition.revision}
                {row.stale ? ` · ${t('来源已变，需重新复核')}` : ''}
              </p>
              <div className="studio-actions">
                <Link href={scopedUrl(`/objects/${row.edition.id}`, workspace)}>
                  {t('阅读原文')}
                </Link>
                <Action
                  secondary
                  onClick={() => {
                    setItem(row)
                    setMode('decision')
                    setStage('abstract')
                    setDecision(row.decisions.abstract?.data.decision || 'uncertain')
                    setReason(row.decisions.abstract?.data.reason || '')
                    setReviewed(false)
                  }}
                >
                  {t('筛选')}
                </Action>
                <Action
                  secondary
                  disabled={row.state !== 'included'}
                  onClick={() =>
                    void op.run(async () => {
                      const ev = await records<EvidenceData>(workspace, 'evidence')
                      setEvidence(ev.filter((e) => e.data.edition_id === row.edition.id))
                      setItem(row)
                      setMode('extraction')
                      setField(report.protocol.data.extraction_fields[0])
                      setValue('')
                      setAnchors([])
                    })
                  }
                >
                  {t('提取证据')}
                </Action>
              </div>
              {Object.entries(row.extractions)
                .filter(([, v]) => v)
                .map(([name, entry]) => (
                  <p key={name}>
                    <strong>{name}</strong>：{entry?.data.value}
                    {entry?.stale && (
                      <span className="studio-muted"> · {t('来源已变，需重新复核')}</span>
                    )}
                    {entry?.data.evidence_ids.map((id, index) => (
                      <Link
                        className="ml-2 underline"
                        key={id}
                        href={scopedUrl(`/objects/${id}`, workspace)}
                      >
                        {t('原文证据')} {index + 1}
                      </Link>
                    ))}
                  </p>
                ))}
            </article>
          ))}
        </>
      )}
      <Dialog open={create} title={t('冻结综述协议')} onClose={() => setCreate(false)} size="lg">
        <form
          className="studio-form"
          onSubmit={(e) => {
            e.preventDefault()
            void op.run(async () => {
              const lines = (s: string) =>
                s
                  .split('\n')
                  .map((x) => x.trim())
                  .filter(Boolean)
              const p = await post<StudioRecord<Protocol>>(workspace, '/reviews/protocols', {
                ...draft,
                inclusion: lines(draft.inclusion),
                exclusion: lines(draft.exclusion),
                extraction_fields: lines(draft.extraction_fields),
                edition_ids: selected,
              })
              setProtocols((old) => [p, ...old])
              setCreate(false)
              await load(p.id)
            })
          }}
        >
          {Object.entries({
            title: t('综述名称'),
            question: t('研究问题'),
            search_query: t('检索式'),
            inclusion: t('纳入标准，每行一条'),
            exclusion: t('排除标准，每行一条'),
            extraction_fields: t('提取字段，每行一个'),
          }).map(([key, label]) => (
            <Field key={key} label={label}>
              <textarea
                required
                value={draft[key as keyof typeof draft]}
                onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
              />
            </Field>
          ))}
          <fieldset className="studio-field">
            <legend>{t('候选文献')}</legend>
            {works.flatMap((w) =>
              w.editions.map((e) => (
                <label className="studio-check" key={e.id}>
                  <input
                    type="checkbox"
                    checked={selected.includes(e.id)}
                    onChange={(ev) =>
                      setSelected((old) =>
                        ev.target.checked ? [...old, e.id] : old.filter((id) => id !== e.id),
                      )
                    }
                  />
                  {w.data.title} · {e.data.label || e.data.citekey || e.id.slice(0, 8)}
                </label>
              )),
            )}
          </fieldset>
          {cursor && (
            <Action secondary onClick={() => void op.run(() => loadPapers(cursor))}>
              {t('加载更多文献')}
            </Action>
          )}
          <button className="studio-button" disabled={op.busy || !selected.length}>
            {t('冻结并开始筛选')}
          </button>
        </form>
      </Dialog>
      <Dialog
        open={!!item}
        title={mode === 'decision' ? t('记录筛选决定') : t('提取原文证据')}
        onClose={() => setItem(null)}
        size="lg"
      >
        {item && report && (
          <form
            className="studio-form"
            onSubmit={(e) => {
              e.preventDefault()
              void op.run(async () => {
                if (mode === 'decision')
                  await post(workspace, `/reviews/protocols/${report.protocol.id}/decisions`, {
                    edition_id: item.edition.id,
                    expected_edition_revision: item.edition.revision,
                    stage,
                    decision,
                    reason,
                    source_reviewed: reviewed,
                    expected_previous_id: item.decisions[stage]?.id || '',
                  })
                else
                  await post(workspace, `/reviews/protocols/${report.protocol.id}/extractions`, {
                    edition_id: item.edition.id,
                    expected_edition_revision: item.edition.revision,
                    field,
                    value,
                    evidence_ids: anchors,
                    expected_previous_id: item.extractions[field]?.id || '',
                  })
                setItem(null)
                await load(report.protocol.id)
              })
            }}
          >
            <strong>{item.title}</strong>
            {mode === 'decision' ? (
              <>
                <Field label={t('阶段')}>
                  <select
                    value={stage}
                    onChange={(e) => {
                      setStage(e.target.value)
                      setDecision(item.decisions[e.target.value]?.data.decision || 'uncertain')
                      setReason(item.decisions[e.target.value]?.data.reason || '')
                      setReviewed(false)
                    }}
                  >
                    <option value="abstract">{t('标题与摘要')}</option>
                    <option value="full_text">{t('全文')}</option>
                  </select>
                </Field>
                <Field label={t('决定')}>
                  <select value={decision} onChange={(e) => setDecision(e.target.value)}>
                    <option value="include">{t('纳入')}</option>
                    <option value="exclude">{t('排除')}</option>
                    <option value="uncertain">{t('待定')}</option>
                  </select>
                </Field>
                <Field label={t('对应标准与理由')}>
                  <textarea
                    required
                    minLength={3}
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                  />
                </Field>
                <label className="studio-check">
                  <input
                    type="checkbox"
                    checked={reviewed}
                    onChange={(e) => setReviewed(e.target.checked)}
                  />
                  {t('我已阅读该阶段的原始材料，并据此作出决定。')}
                </label>
              </>
            ) : (
              <>
                <Field label={t('提取字段')}>
                  <select
                    value={field}
                    onChange={(e) => {
                      setField(e.target.value)
                      setValue(item.extractions[e.target.value]?.data.value || '')
                      setAnchors(item.extractions[e.target.value]?.data.evidence_ids || [])
                    }}
                  >
                    {report.protocol.data.extraction_fields.map((f) => (
                      <option key={f}>{f}</option>
                    ))}
                  </select>
                </Field>
                <Field label={t('提取结果')}>
                  <textarea required value={value} onChange={(e) => setValue(e.target.value)} />
                </Field>
                <fieldset className="studio-field">
                  <legend>{t('对应原文证据')}</legend>
                  {evidence.map((ev) => (
                    <label className="studio-check" key={ev.id}>
                      <input
                        type="checkbox"
                        checked={anchors.includes(ev.id)}
                        onChange={(e) =>
                          setAnchors((old) =>
                            e.target.checked ? [...old, ev.id] : old.filter((id) => id !== ev.id),
                          )
                        }
                      />
                      {ev.data.label || ev.data.quote.slice(0, 140)} · {t('页')} {ev.data.page}
                    </label>
                  ))}
                </fieldset>
                {!evidence.length && <p>{t('请先在原文中建立证据选区，再回来提取。')}</p>}
              </>
            )}
            <button
              className="studio-button"
              disabled={op.busy || (mode === 'decision' ? !reviewed : !anchors.length)}
            >
              {t('保存带来源的记录')}
            </button>
          </form>
        )}
      </Dialog>
    </section>
  )
}
