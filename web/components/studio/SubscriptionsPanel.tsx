'use client'
import { useEffect, useState } from 'react'
import { BookmarkPlus, RefreshCw, Rss } from 'lucide-react'
import { post, records, studio, type StudioRecord } from '@/lib/studio-api'
import { Action, Field, ErrorNotice, useOperation } from './StudioShell'
type Subscription = {
  title: string
  kind: 'search' | 'rss'
  query: string
  providers: string[]
  url: string
  order?: string
  year_from?: number | null
  year_to?: number | null
  enabled: boolean
  interval_hours: number
  last_status?: string
  last_check?: number
  last_discovery_id?: string
  new_ids?: string[]
}
export default function SubscriptionsPanel({
  workspace,
  search,
  providers,
  order,
  yearFrom,
  yearTo,
  openResult,
}: {
  workspace: string
  search: string
  providers: string[]
  order: string
  yearFrom: number | null
  yearTo: number | null
  openResult: (id: string) => Promise<void>
}) {
  const [items, setItems] = useState<StudioRecord<Subscription>[]>([]),
    [expanded, setExpanded] = useState(false),
    [rss, setRss] = useState(false),
    [title, setTitle] = useState(''),
    [url, setUrl] = useState(''),
    [notice, setNotice] = useState('')
  const op = useOperation()
  useEffect(() => {
    const abort = new AbortController()
    records<Subscription>(workspace, 'subscription', abort.signal)
      .then(setItems)
      .catch(e => {
        if (!abort.signal.aborted) op.setError(e.message)
      })
    return () => abort.abort()
  }, [workspace]) // eslint-disable-line react-hooks/exhaustive-deps
  async function reload() {
    setItems(await records<Subscription>(workspace, 'subscription'))
  }
  async function saveSearch() {
    await post(workspace, '/subscriptions', {
      title: search,
      query: search,
      providers,
      order,
      year_from: yearFrom,
      year_to: yearTo,
      kind: 'search',
    })
    await reload()
    setExpanded(true)
    setNotice('检索已保存。自动刷新默认关闭，可以按需开启。')
  }
  async function saveFeed() {
    await post(workspace, '/subscriptions', { title, kind: 'rss', url })
    await reload()
    setRss(false)
    setTitle('')
    setUrl('')
    setNotice('订阅源已保存。点击刷新获取第一份快照。')
  }
  async function toggle(item: StudioRecord<Subscription>) {
    const { title, kind, query, providers, url, interval_hours, order, year_from, year_to } =
      item.data
    await studio(workspace, `/subscriptions/${item.id}`, {
      method: 'PATCH',
      body: JSON.stringify({
        title,
        kind,
        query,
        providers,
        url,
        interval_hours,
        order: order || 'relevance',
        year_from,
        year_to,
        enabled: !item.data.enabled,
        expected_revision: item.revision,
      }),
    })
    await reload()
  }
  return (
    <div className="studio-subscriptions">
      <div className="studio-actions">
        <Action secondary onClick={() => setExpanded(!expanded)}>
          <Rss size={13} />
          保存的检索与订阅 · {items.length}
        </Action>
        <Action
          secondary
          disabled={op.busy || !search.trim() || !providers.length}
          onClick={() => op.run(saveSearch)}
        >
          <BookmarkPlus size={13} />
          保存当前检索
        </Action>
      </div>
      <ErrorNotice message={op.error} />
      {notice && (
        <p className="studio-note" role="status">
          {notice}
        </p>
      )}
      {expanded && (
        <>
          <p className="studio-note">
            开启自动刷新后，软件运行期间每 24
            小时访问一次对应来源。新题录进入检索快照，是否收藏和纳入研究仍由你决定。
          </p>
          {items.map(item => (
            <article className="studio-card" key={item.id} style={{ marginBottom: 10 }}>
              <h3>{item.data.title}</h3>
              <p className="studio-note">
                {item.data.kind === 'rss' ? 'RSS / Atom' : item.data.providers.join(' · ')} ·{' '}
                {item.data.enabled ? '每天自动刷新' : '手动刷新'}
                {item.data.last_check
                  ? ` · 上次 ${new Date(item.data.last_check * 1000).toLocaleString()}`
                  : ''}
              </p>
              {item.data.last_status && (
                <p className="studio-note">
                  {item.data.last_status === 'failed'
                    ? '上次刷新未成功，旧结果仍保留。'
                    : `${item.data.new_ids?.length || 0} 条此前未见的题录；本次未必覆盖全部结果。`}
                </p>
              )}
              <footer>
                <div className="studio-actions">
                  <Action
                    secondary
                    disabled={op.busy}
                    onClick={() =>
                      op.run(async () => {
                        await post(workspace, `/subscriptions/${item.id}/refresh`, {})
                        await reload()
                      })
                    }
                  >
                    <RefreshCw size={12} />
                    刷新
                  </Action>
                  <Action secondary disabled={op.busy} onClick={() => op.run(() => toggle(item))}>
                    {item.data.enabled ? '暂停自动刷新' : '开启自动刷新'}
                  </Action>
                </div>
                {item.data.last_discovery_id && (
                  <Action
                    secondary
                    disabled={op.busy}
                    onClick={() => op.run(() => openResult(item.data.last_discovery_id!))}
                  >
                    打开快照
                  </Action>
                )}
              </footer>
            </article>
          ))}
          {rss ? (
            <div className="studio-panel">
              <Field label="订阅名称">
                <input value={title} onChange={e => setTitle(e.target.value)} />
              </Field>
              <Field label="公开 HTTPS RSS / Atom 地址">
                <input
                  type="url"
                  value={url}
                  onChange={e => setUrl(e.target.value)}
                  placeholder="https://…"
                />
              </Field>
              <div className="studio-actions">
                <Action disabled={!title || !url || op.busy} onClick={() => op.run(saveFeed)}>
                  保存订阅源
                </Action>
                <Action secondary onClick={() => setRss(false)}>
                  取消
                </Action>
              </div>
            </div>
          ) : (
            <Action secondary onClick={() => setRss(true)}>
              添加 RSS / Atom 订阅源
            </Action>
          )}
        </>
      )}
    </div>
  )
}
