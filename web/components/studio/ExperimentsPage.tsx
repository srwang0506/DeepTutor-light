'use client'
import { useTranslation } from 'react-i18next'
import IntegrationSources from './IntegrationSources'
import LiveRunTimeline from './LiveRunTimeline'
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { ArrowUpRight, Download, FlaskConical, Plus, Upload } from 'lucide-react'
import { useActiveWorkspaceId } from '@/hooks/useActiveWorkspaceId'
import { Dialog } from '@/shared/ui/Dialog'
import { scopedUrl } from '@/lib/workspace-scope'
import { post, records, studio, type StudioRecord } from '@/lib/studio-api'
import { Action, ErrorNotice, Field, StudioEmpty, StudioShell, useOperation } from './StudioShell'
interface Run {
  title: string
  status: string
  execution_verified?: boolean
  execution_witness?: string
  repository: string
  commit: string
  patch: string
  command: string
  configuration: Record<string, unknown>
  dataset: string
  split: string
  environment: string
  hardware: string
  seed: number | null
  checkpoint: string
  log: string
  artifacts: Record<string, string>[]
  missing?: string[]
  remote_source?: {
    target: string
    path: string
    content_hash: string
    repository_observed_commit?: string
  }
  tracking_snapshot?: {
    adapter: string
    external_id: string
    declared_status: string | null
    metric_samples: { name: string; value: number; step?: number; timestamp?: number }[]
  }
}
const blank = (): Run => ({
  title: '',
  status: 'planned',
  repository: '',
  commit: '',
  patch: '',
  command: '',
  configuration: {},
  dataset: '',
  split: '',
  environment: '',
  hardware: '',
  seed: null,
  checkpoint: '',
  log: '',
  artifacts: [],
})
const statusLabels: Record<string, string> = {
  planned: '待运行 / 核对',
  running: '运行中',
  succeeded: '运行成功',
  failed: '运行失败',
  cancelled: '已取消',
  static_only: '静态检查',
}
const fieldLabels: Record<string, string> = {
  repository: '代码仓库',
  commit: '运行 commit',
  command: '运行命令',
  configuration: '配置',
  dataset: '数据集',
  split: '数据划分',
  environment: '环境',
  hardware: '硬件',
  seed: '随机种子',
  checkpoint: '检查点',
  log: '原始日志',
}
export default function ExperimentsPage() {
  const workspace = useActiveWorkspaceId()
  return <Experiments key={workspace} workspace={workspace} />
}
function Experiments({ workspace }: { workspace: string }) {
  const { t } = useTranslation()

  const op = useOperation(),
    [runs, setRuns] = useState<StudioRecord<Run>[]>([]),
    [selected, setSelected] = useState<StudioRecord<Run> | null>(null),
    [filter, setFilter] = useState('all'),
    [dialog, setDialog] = useState(false),
    [draft, setDraft] = useState(blank),
    [config, setConfig] = useState('{}'),
    [editing, setEditing] = useState<StudioRecord<Run> | null>(null),
    [importing, setImporting] = useState(false),
    [input, setInput] = useState(''),
    [format, setFormat] = useState('json'),
    [result, setResult] = useState<{
      entries: { index: number; status: string; detail?: string }[]
    } | null>(null)
  async function reload(signal?: AbortSignal) {
    setRuns(await records<Run>(workspace, 'run', signal))
  }
  useEffect(() => {
    const a = new AbortController()
    records<Run>(workspace, 'run', a.signal)
      .then(setRuns)
      .catch((e) => {
        if (!a.signal.aborted) op.setError(e.message)
      })
    return () => a.abort()
  }, [workspace]) // eslint-disable-line react-hooks/exhaustive-deps
  function edit(row?: StudioRecord<Run>) {
    setEditing(row || null)
    const data = row
      ? (Object.fromEntries(
          Object.keys(blank()).map((key) => [key, row.data[key as keyof Run]]),
        ) as unknown as Run)
      : blank()
    setDraft(data)
    setConfig(JSON.stringify(data.configuration, null, 2))
    setDialog(true)
  }
  async function save() {
    const configuration = JSON.parse(config)
    if (!configuration || typeof configuration !== 'object' || Array.isArray(configuration))
      throw new Error('配置需要是 JSON 对象。')
    const body = { ...draft, configuration }
    const row = editing
      ? await studio<StudioRecord<Run>>(workspace, `/runs/${editing.id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...body, expected_revision: editing.revision }),
        })
      : await post<StudioRecord<Run>>(workspace, '/runs', body)
    setDialog(false)
    setSelected(row)
    await reload()
  }
  async function importRows() {
    setResult(await post(workspace, '/runs/import', { text: input, format }))
    await reload()
  }
  function download(row: StudioRecord<Run>) {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify({ schema: 'deeptutor-run-v1', record: row }, null, 2)], {
        type: 'application/json',
      }),
    )
    const a = document.createElement('a')
    a.href = url
    a.download = `run-${row.id}.json`
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  return (
    <StudioShell
      workspace={workspace}
      active="experiments"
      title={t('把一次实验，留成可复查的记录。')}
      description="代码版本、命令、环境和原始产物一起保存。成功、失败与静态检查，都有各自的位置。"
      action={
        <div className="studio-actions">
          <Action
            secondary
            onClick={() => {
              setResult(null)
              setImporting(true)
            }}
          >
            <Upload size={14} />
            {t('导入记录')}
          </Action>
          <Action onClick={() => edit()}>
            <Plus size={14} />
            {t('记录实验')}
          </Action>
        </div>
      }
    >
      <IntegrationSources
        workspace={workspace}
        provider="local_log"
        onChange={() => void reload()}
      />
      <ErrorNotice message={op.error} />
      <div className="studio-toolbar">
        <div className="studio-pills">
          {[
            ['all', '全部'],
            ['succeeded', '已成功'],
            ['failed', '需排查'],
            ['static_only', '静态检查'],
          ].map(([key, title]) => (
            <button key={key} aria-pressed={filter === key} onClick={() => setFilter(key)}>
              {title}
            </button>
          ))}
        </div>
        <Link className="studio-outlink" href={scopedUrl('/studio/connections', workspace)}>
          {t('从 SSH 或 GitHub 读取')}
          <ArrowUpRight size={13} />
        </Link>
      </div>
      <div className="studio-grid studio-experiments-grid">
        <div className="studio-card-list">
          {runs
            .filter((r) => filter === 'all' || r.data.status === filter)
            .map((row) => (
              <article className="studio-card" key={row.id}>
                <div className="studio-actions">
                  <FlaskConical size={15} />
                  <span
                    className={`studio-badge ${row.data.status === 'succeeded' && row.data.execution_verified ? 'good' : row.data.status === 'failed' ? 'warn' : ''}`}
                  >
                    {statusLabels[row.data.status]}
                    {row.data.status === 'succeeded' && !row.data.execution_verified
                      ? ' · 来源自述'
                      : ''}
                  </span>
                  {row.data.remote_source && (
                    <span className="studio-badge">{t('远程快照导入')}</span>
                  )}
                </div>
                <h3 style={{ marginTop: 12 }}>
                  <button style={{ textAlign: 'left' }} onClick={() => setSelected(row)}>
                    {row.data.title}
                  </button>
                </h3>
                <p>
                  {row.data.dataset || '数据集待确认'} · {row.data.split || '划分待确认'}
                </p>
                <p className="studio-note">
                  <code>{row.data.commit ? row.data.commit.slice(0, 12) : '运行版本未知'}</code> ·{' '}
                  {row.data.missing?.length
                    ? `${row.data.missing.length} 项复现信息待补全`
                    : '元信息已填写，输入可获得性仍需核实'}
                </p>
                <footer>
                  <button className="studio-link-button" onClick={() => setSelected(row)}>
                    {t('查看原始记录')}
                    <ArrowUpRight size={12} />
                  </button>
                  <button className="studio-link-button" onClick={() => edit(row)}>
                    {t('补全 / 编辑')}
                  </button>
                </footer>
              </article>
            ))}
          {!runs.length && (
            <StudioEmpty title={t('记录真实的实验过程')}>
              {t(
                '可以手动填写，也可以导入 CSV / JSON，或从服务器抓取日志快照。导入行为本身不会被记作成功运行。',
              )}
            </StudioEmpty>
          )}
        </div>
        <aside>
          {selected ? (
            <div className="studio-panel">
              <LiveRunTimeline key={selected.id} workspace={workspace} runId={selected.id} />
              <div className="studio-actions" style={{ justifyContent: 'space-between' }}>
                <h2 style={{ margin: 0 }}>{selected.data.title}</h2>
                <button className="studio-link-button" onClick={() => download(selected)}>
                  <Download size={12} />
                  {t('导出')}
                </button>
              </div>
              <p className="studio-note">
                {statusLabels[selected.data.status]} {t('· 修订')}
                {selected.revision}
              </p>
              {selected.data.missing?.length ? (
                <div className="studio-badges">
                  {selected.data.missing.map((key) => (
                    <span className="studio-badge warn" key={key}>
                      {fieldLabels[key] || key}
                      {t('待补')}
                    </span>
                  ))}
                </div>
              ) : null}
              <h3 className="studio-section-title">{t('运行来源')}</h3>
              <p className="studio-note">
                {selected.data.execution_verified
                  ? '系统已记录执行见证。'
                  : selected.data.execution_witness === 'imported_statement' ||
                      selected.data.remote_source
                    ? '状态来自导入记录；系统未观察该进程执行。'
                    : '状态来自作者记录；系统未观察该进程执行。'}
              </p>
              <p style={{ overflowWrap: 'anywhere' }}>
                {selected.data.repository || '尚未记录仓库'}
              </p>
              <p>
                <code>{selected.data.commit || '运行 commit 未知'}</code>
              </p>
              {selected.data.tracking_snapshot &&
                selected.data.tracking_snapshot.adapter !== 'plain_log' && (
                  <section className="studio-panel" style={{ marginBottom: 16 }}>
                    <h3 className="studio-section-title">
                      {t('外部运行快照 ·')}
                      {selected.data.tracking_snapshot.adapter}
                    </h3>
                    <p className="studio-note">
                      {t('来源 ID：')}
                      {selected.data.tracking_snapshot.external_id || '未提供'}{' '}
                      {t('· 来源声明状态：')}
                      {selected.data.tracking_snapshot.declared_status || '未知'}
                    </p>
                    <p className="studio-note">
                      {t('指标样本保留原名与步骤；进入比较矩阵前仍需核对评估协议。')}
                    </p>
                    <div className="studio-source-picker">
                      {selected.data.tracking_snapshot.metric_samples
                        .slice(0, 50)
                        .map((metric, i) => (
                          <div className="studio-outline-row" key={i}>
                            {metric.name} · {metric.value}
                            <small>
                              {t('step')}
                              {metric.step ?? '未知'}
                            </small>
                          </div>
                        ))}
                    </div>
                    {selected.data.tracking_snapshot.metric_samples.length > 50 && (
                      <p className="studio-note">{t('显示前 50 条，导出记录包含全部样本。')}</p>
                    )}
                  </section>
                )}
              {selected.data.remote_source && (
                <>
                  <p className="studio-note" style={{ overflowWrap: 'anywhere' }}>
                    {t('读取自')}
                    {selected.data.remote_source.target}:{selected.data.remote_source.path}
                  </p>
                  <p className="studio-note" style={{ overflowWrap: 'anywhere' }}>
                    {t('SHA-256')}
                    {selected.data.remote_source.content_hash}
                  </p>
                  {selected.data.remote_source.repository_observed_commit && (
                    <p className="studio-note">
                      {t('读取时 HEAD：')}
                      {selected.data.remote_source.repository_observed_commit.slice(0, 12)}
                      {t('。这是观测版本，需要另行核对实验运行版本。')}
                    </p>
                  )}
                </>
              )}
              <h3 className="studio-section-title">{t('命令与配置')}</h3>
              <pre className="studio-diff">
                {selected.data.command || '运行命令尚未记录'}
                {'\n\n'}
                {JSON.stringify(selected.data.configuration, null, 2)}
              </pre>
              <h3 className="studio-section-title">{t('原始日志')}</h3>
              <pre className="studio-diff">{selected.data.log || '尚无原始日志'}</pre>
            </div>
          ) : (
            <div className="studio-panel">
              <h2>{t('复现，需要完整的上下文。')}</h2>
              <p>
                {t(
                  '选中左侧记录，可以查看运行命令、原始配置、日志和来源。元信息缺失不会被默认值掩盖。',
                )}
              </p>
              <p className="studio-note">
                {t('实验结果可以在“证据与比较”中和文献报告放在一起，按同一套评估条件核对。')}
              </p>
            </div>
          )}
        </aside>
      </div>
      <Dialog
        open={dialog}
        onClose={() => !op.busy && setDialog(false)}
        title={editing ? '补全实验记录' : '记录一次实验'}
        description="填写真实运行信息。尚未掌握的字段可以留空。"
        size="lg"
        className="studio-dialog"
        footer={
          <Action disabled={op.busy || !draft.title.trim()} onClick={() => op.run(save)}>
            {t('保存记录')}
          </Action>
        }
      >
        <div className="studio-form">
          <ErrorNotice message={op.error} />
          <Field label="名称">
            <input
              value={draft.title}
              onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))}
            />
          </Field>
          <Field label="状态">
            <select
              value={draft.status}
              onChange={(e) => setDraft((d) => ({ ...d, status: e.target.value }))}
            >
              {Object.entries(statusLabels).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
          <div className="studio-form-grid">
            {(['repository', 'commit', 'dataset', 'split', 'hardware', 'checkpoint'] as const).map(
              (key) => (
                <Field key={key} label={fieldLabels[key]}>
                  <input
                    value={draft[key]}
                    onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
                  />
                </Field>
              ),
            )}
            <Field label="随机种子">
              <input
                type="number"
                step="1"
                value={draft.seed ?? ''}
                onChange={(e) =>
                  setDraft((d) => ({
                    ...d,
                    seed: e.target.value === '' ? null : Number(e.target.value),
                  }))
                }
              />
            </Field>
          </div>
          <Field label="运行命令">
            <textarea
              value={draft.command}
              onChange={(e) => setDraft((d) => ({ ...d, command: e.target.value }))}
            />
          </Field>
          <Field label="配置 · JSON">
            <textarea
              rows={6}
              value={config}
              onChange={(e) => setConfig(e.target.value)}
              spellCheck={false}
            />
          </Field>
          <Field label="环境与依赖">
            <textarea
              value={draft.environment}
              onChange={(e) => setDraft((d) => ({ ...d, environment: e.target.value }))}
            />
          </Field>
          <Field label="实际代码补丁">
            <textarea
              value={draft.patch}
              onChange={(e) => setDraft((d) => ({ ...d, patch: e.target.value }))}
              spellCheck={false}
            />
          </Field>
          <Field label="原始日志">
            <textarea
              rows={7}
              value={draft.log}
              onChange={(e) => setDraft((d) => ({ ...d, log: e.target.value }))}
              spellCheck={false}
            />
          </Field>
        </div>
      </Dialog>
      <Dialog
        open={importing}
        onClose={() => !op.busy && setImporting(false)}
        title={t('导入实验记录')}
        description="每行一条记录。字段使用 title、status、repository、commit、command、dataset、split、seed 等名称。"
        size="lg"
        className="studio-dialog"
        footer={
          <Action disabled={op.busy || !input.trim()} onClick={() => op.run(importRows)}>
            {t('解析并导入')}
          </Action>
        }
      >
        <div className="studio-form">
          <ErrorNotice message={op.error} />
          <Field label="文件">
            <input
              type="file"
              accept=".json,.csv"
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f)
                  op.run(async () => {
                    if (f.size > 5_000_000) throw new Error('请选择 5 MB 以内的文件。')
                    setInput(await f.text())
                    setFormat(f.name.endsWith('.csv') ? 'csv' : 'json')
                  })
              }}
            />
          </Field>
          <Field label="格式">
            <select value={format} onChange={(e) => setFormat(e.target.value)}>
              <option value="json">{t('JSON')}</option>
              <option value="csv">{t('CSV')}</option>
            </select>
          </Field>
          <Field label="记录内容">
            <textarea
              rows={8}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              spellCheck={false}
            />
          </Field>
          {result && (
            <div className="studio-card-list">
              {result.entries.map((row) => (
                <p
                  key={row.index}
                  className={row.status === 'error' ? 'studio-error' : 'studio-note'}
                >
                  第 {row.index + 1} {t('条：')}
                  {row.status === 'error' ? row.detail : '已导入'}
                </p>
              ))}
            </div>
          )}
        </div>
      </Dialog>
    </StudioShell>
  )
}
