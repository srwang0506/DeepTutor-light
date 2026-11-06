'use client'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, RefreshCw, Unplug, LibraryBig, Activity } from 'lucide-react'
import Link from 'next/link'
import { Dialog } from '@/shared/ui/Dialog'
import { scopedUrl } from '@/lib/workspace-scope'
import { post, studio, type StudioRecord } from '@/lib/studio-api'
import { Action, ErrorNotice, Field, useOperation } from './StudioShell'
interface Source {
  title: string
  provider: 'zotero' | 'local_log'
  library_type: 'users' | 'groups'
  library_id: string
  path: string
  enabled: boolean
  interval_seconds: number
  state: string
  error?: string
  last_sync?: number
  last_count?: number
  cursor: { version?: number; offset?: number }
}
export default function IntegrationSources({
  workspace,
  provider,
  onChange,
}: {
  workspace: string
  provider: Source['provider']
  onChange?: () => void
}) {
  const { t } = useTranslation()
  const op = useOperation()
  const [loadError, setLoadError] = useState('')
  const [sources, setSources] = useState<StudioRecord<Source>[]>([])
  const [dialog, setDialog] = useState(false)
  const [editing, setEditing] = useState<StudioRecord<Source> | null>(null)
  const [draft, setDraft] = useState({
    title: '',
    library_type: 'users',
    library_id: '',
    path: '',
    api_key: '',
  })
  const [objects, setObjects] = useState<
    { key: string; record_id: string | null; deleted: number; version: number }[] | null
  >(null)
  const [objectSource, setObjectSource] = useState('')
  const [objectCursor, setObjectCursor] = useState('')
  const load = () =>
    studio<{ sources: StudioRecord<Source>[] }>(workspace, '/integrations').then((r) =>
      setSources(r.sources.filter((s) => s.data.provider === provider)),
    )
  useEffect(() => {
    let active = true
    const refresh = () =>
      studio<{ sources: StudioRecord<Source>[] }>(workspace, '/integrations')
        .then((r) => {
          if (active) {
            setSources(r.sources.filter((s) => s.data.provider === provider))
            setLoadError('')
          }
        })
        .catch((e) => {
          if (active) setLoadError(String(e))
        })
    void refresh()
    const timer = setInterval(refresh, 15000)
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [workspace, provider])
  const stateName: Record<string, string> = {
    current: t('已同步'),
    ready: t('待同步'),
    paused: t('已断开'),
    retrying: t('等待重试'),
    unauthorized: t('需要重新授权'),
  }
  return (
    <section
      className="studio-card studio-feature"
      aria-label={provider === 'zotero' ? t('Zotero 增量同步') : t('实时实验来源')}
    >
      <div className="studio-toolbar">
        <div className="studio-panel-heading">
          <span className="studio-panel-icon">
            {provider === 'zotero' ? (
              <LibraryBig size={19} aria-hidden />
            ) : (
              <Activity size={19} aria-hidden />
            )}
          </span>
          <div>
            <h3>{provider === 'zotero' ? t('连接 Zotero 书库') : t('持续接收实验记录')}</h3>
            <p className="studio-muted">
              {provider === 'zotero'
                ? t('只读同步文献元数据，保留作者整理与来源删除标记。')
                : t('从选定 JSONL 文件断点续读。运行声明、指标与产物保留原始事件。')}
            </p>
          </div>
        </div>
        <Action
          secondary
          onClick={() => {
            setEditing(null)
            setDraft({ title: '', library_type: 'users', library_id: '', path: '', api_key: '' })
            setDialog(true)
          }}
        >
          <Plus size={15} />
          {t('连接来源')}
        </Action>
      </div>
      <ErrorNotice message={loadError || op.error} />
      {sources.length > 0 && (
        <div className="studio-inset-list">
          {sources.map((source) => (
            <article key={source.id} className="studio-card">
              <div className="studio-toolbar">
                <div>
                  <strong>{source.data.title}</strong>
                  <p className="studio-muted">
                    {stateName[source.data.state] || source.data.state} ·{' '}
                    {source.data.last_sync
                      ? new Date(source.data.last_sync * 1000).toLocaleString()
                      : t('尚未同步')}
                  </p>
                  <p className="studio-muted">
                    {source.data.provider === 'zotero'
                      ? `${t('库版本')} ${source.data.cursor.version ?? 0}`
                      : `${t('已读取字节')} ${source.data.cursor.offset ?? 0}`}{' '}
                    · {t('最近新增')} {source.data.last_count ?? 0}
                  </p>
                  {source.data.error && <ErrorNotice message={source.data.error} />}
                </div>
                <div className="studio-actions">
                  <Action
                    secondary
                    disabled={op.busy || !source.data.enabled}
                    onClick={() =>
                      void op.run(async () => {
                        await post(workspace, `/integrations/${source.id}/refresh`, {})
                        await load()
                        onChange?.()
                      })
                    }
                  >
                    <RefreshCw size={14} />
                    {t('立即同步')}
                  </Action>
                  <Action
                    secondary
                    onClick={() => {
                      setEditing(source)
                      setDraft({
                        title: source.data.title,
                        library_type: source.data.library_type,
                        library_id: source.data.library_id,
                        path: source.data.path,
                        api_key: '',
                      })
                      setDialog(true)
                    }}
                  >
                    {t('重新连接')}
                  </Action>
                  <Action
                    secondary
                    disabled={op.busy || !source.data.enabled}
                    onClick={() =>
                      void op.run(async () => {
                        await studio(workspace, `/integrations/${source.id}`, {
                          method: 'PATCH',
                          body: JSON.stringify({
                            title: source.data.title,
                            provider: source.data.provider,
                            library_type: source.data.library_type,
                            library_id: source.data.library_id,
                            path: source.data.path,
                            interval_seconds: source.data.interval_seconds,
                            enabled: false,
                            expected_revision: source.revision,
                          }),
                        })
                        await load()
                      })
                    }
                  >
                    <Unplug size={14} />
                    {t('断开')}
                  </Action>
                  {provider === 'zotero' && (
                    <Action
                      secondary
                      onClick={() =>
                        void op.run(async () => {
                          const r = await studio<{
                            items: NonNullable<typeof objects>
                            next_cursor: string
                          }>(workspace, `/integrations/${source.id}/objects`)
                          setObjects(r.items)
                          setObjectSource(source.id)
                          setObjectCursor(r.next_cursor)
                        })
                      }
                    >
                      {t('来源条目')}
                    </Action>
                  )}
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
      {dialog && (
        <Dialog
          open
          className="studio-dialog"
          busy={op.busy}
          title={t('连接只读来源')}
          onClose={() => setDialog(false)}
        >
          <form
            className="studio-form"
            onSubmit={(e) => {
              e.preventDefault()
              void op.run(async () => {
                const payload = {
                  ...draft,
                  provider,
                  enabled: true,
                  interval_seconds: provider === 'zotero' ? 300 : 10,
                }
                if (editing)
                  await studio(workspace, `/integrations/${editing.id}`, {
                    method: 'PATCH',
                    body: JSON.stringify({ ...payload, expected_revision: editing.revision }),
                  })
                else await post(workspace, '/integrations', payload)
                setDraft((d) => ({ ...d, api_key: '' }))
                setDialog(false)
                await load()
              })
            }}
          >
            <ErrorNotice message={op.error} />
            <Field label={t('名称')}>
              <input
                required
                value={draft.title}
                onChange={(e) => setDraft({ ...draft, title: e.target.value })}
              />
            </Field>
            {provider === 'zotero' ? (
              <>
                <Field label={t('库类型')}>
                  <select
                    disabled={!!editing}
                    value={draft.library_type}
                    onChange={(e) => setDraft({ ...draft, library_type: e.target.value })}
                  >
                    <option value="users">{t('个人书库')}</option>
                    <option value="groups">{t('群组书库')}</option>
                  </select>
                </Field>
                <Field label={t('Zotero Library ID')}>
                  <input
                    required
                    disabled={!!editing}
                    inputMode="numeric"
                    value={draft.library_id}
                    onChange={(e) => setDraft({ ...draft, library_id: e.target.value })}
                  />
                </Field>
                <Field
                  label={t('只读 API Key')}
                  hint={t('凭据加密保存在这台设备上，不进入项目同步包。断开后会清除凭据。')}
                >
                  <input
                    type="password"
                    required
                    autoComplete="off"
                    value={draft.api_key}
                    onChange={(e) => setDraft({ ...draft, api_key: e.target.value })}
                  />
                </Field>
              </>
            ) : (
              <>
                <Field label={t('JSONL 日志的绝对路径')}>
                  <input
                    required
                    disabled={!!editing}
                    value={draft.path}
                    onChange={(e) => setDraft({ ...draft, path: e.target.value })}
                  />
                </Field>
                <p className="studio-muted">
                  {t(
                    '每行一个事件，含 event_id、run_id、type。metric 还需 name、value、step。事件 ID 不可复用来改写内容；订正请追加新事件。',
                  )}
                </p>
              </>
            )}
            <button className="studio-button" disabled={op.busy}>
              {t('保存连接')}
            </button>
          </form>
        </Dialog>
      )}
      {objects && (
        <Dialog
          open
          className="studio-dialog"
          title={t('来源条目与删除标记')}
          onClose={() => setObjects(null)}
        >
          <div className="studio-form">
            <ErrorNotice message={op.error} />
            {objectCursor && (
              <Action
                disabled={op.busy}
                onClick={() =>
                  void op.run(async () => {
                    const r = await studio<{
                      items: NonNullable<typeof objects>
                      next_cursor: string
                    }>(
                      workspace,
                      `/integrations/${objectSource}/objects?after=${encodeURIComponent(objectCursor)}`,
                    )
                    setObjects((old) => [...(old || []), ...r.items])
                    setObjectCursor(r.next_cursor)
                  })
                }
              >
                {t('加载更多来源条目')}
              </Action>
            )}
            {objects.map((item) => (
              <div key={item.key}>
                <strong>{item.key}</strong> · {t('版本')} {item.version} ·{' '}
                {item.deleted ? t('来源已删除，本地引用保留') : t('来源有效')}
                {item.record_id && (
                  <Link href={scopedUrl(`/objects/${item.record_id}`, workspace)}>
                    {t('打开记录')}
                  </Link>
                )}
              </div>
            ))}
          </div>
        </Dialog>
      )}
    </section>
  )
}
