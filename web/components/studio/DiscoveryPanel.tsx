'use client'
import { useTranslation } from 'react-i18next'
import { useEffect, useState } from 'react'
import SubscriptionsPanel from './SubscriptionsPanel'
import { ArrowUpRight, History, Search } from 'lucide-react'
import { post, records, studio, type DiscoveryData, type StudioRecord } from '@/lib/studio-api'
import { Action, ErrorNotice, Field, useOperation } from './StudioShell'
const statusText: Record<string, string> = {
  complete: '本次结果已取完',
  partial: '还有后续结果',
  rate_limited: '来源限流',
  authorization_required: '需要来源授权',
  failed: '请求失败',
  running: '读取中',
}
export default function DiscoveryPanel({
  workspace,
  onCollected,
}: {
  workspace: string
  onCollected: () => Promise<void>
}) {
  const { t } = useTranslation()

  const [query, setQuery] = useState(''),
    [providers, setProviders] = useState(['crossref', 'openalex']),
    [order, setOrder] = useState('relevance'),
    [from, setFrom] = useState(''),
    [to, setTo] = useState('')
  const [found, setFound] = useState<StudioRecord<DiscoveryData> | null>(null),
    [history, setHistory] = useState<StudioRecord<DiscoveryData>[]>([]),
    [historyOpen, setHistoryOpen] = useState(false)
  const [notice, setNotice] = useState(''),
    [graph, setGraph] = useState<{
      nodes: { id: string; title: string }[]
      edges: { source: string; target: string }[]
    } | null>(null)
  const op = useOperation()
  useEffect(() => {
    const abort = new AbortController()
    records<DiscoveryData>(workspace, 'discovery', abort.signal)
      .then(setHistory)
      .catch((e) => {
        if (!abort.signal.aborted) op.setError(e.message)
      })
    return () => abort.abort()
  }, [workspace]) // eslint-disable-line react-hooks/exhaustive-deps
  async function search() {
    setGraph(null)
    setNotice('')
    const row = await post<StudioRecord<DiscoveryData>>(workspace, '/discover', {
      query,
      providers,
      order,
      year_from: from ? Number(from) : null,
      year_to: to ? Number(to) : null,
      limit: 15,
    })
    setFound(row)
    setHistory((h) => [row, ...h.filter((r) => r.id !== row.id)])
  }
  const latestPages = found
    ? Object.values(Object.fromEntries(found.data.pages.map((page) => [page.provider, page])))
    : []
  async function next(provider: string) {
    if (!found) return
    const row = await post<StudioRecord<DiscoveryData>>(workspace, `/discover/${found.id}/next`, {
      provider,
      expected_revision: found.revision,
    })
    setFound(row)
    setGraph(null)
    setHistory((h) => h.map((r) => (r.id === row.id ? row : r)))
  }
  async function collect(provider: string, id: string) {
    await post(workspace, `/discover/${found!.id}/collect`, { provider, item_id: id })
    await onCollected()
    setNotice('已收藏，并保存本次检索式、来源和结果版本。')
  }
  const safeUrl = (url: string) => (/^https?:\/\//i.test(url) ? url : undefined)
  return (
    <section className="studio-discovery">
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void op.run(search)
        }}
      >
        <div className="studio-toolbar">
          <label className="studio-search">
            <Search size={16} />
            <input
              aria-label={t('学术检索问题')}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t('例如 human-object interaction')}
            />
          </label>
          <Action
            disabled={op.busy || !query.trim() || !providers.length}
            onClick={() => op.run(search)}
          >
            {op.busy ? '正在查询…' : '查询公开题录'}
          </Action>
        </div>
        <div className="studio-discovery-options">
          <div className="studio-pills">
            {['crossref', 'openalex', 'arxiv'].map((p) => (
              <button
                type="button"
                key={p}
                aria-pressed={providers.includes(p)}
                onClick={() =>
                  setProviders((all) =>
                    all.includes(p) ? all.filter((v) => v !== p) : [...all, p],
                  )
                }
              >
                {p}
              </button>
            ))}
          </div>
          <Field label="排序">
            <select value={order} onChange={(e) => setOrder(e.target.value)}>
              <option value="relevance">{t('相关度')}</option>
              <option value="newest">{t('最新发表')}</option>
            </select>
          </Field>
          <Field label="起始年份">
            <input
              type="number"
              min="1000"
              max="2200"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
            />
          </Field>
          <Field label="截止年份">
            <input
              type="number"
              min="1000"
              max="2200"
              value={to}
              onChange={(e) => setTo(e.target.value)}
            />
          </Field>
        </div>
      </form>
      <p className="studio-note">
        {t(
          '检索词将发送至已选数据源。逐页保存实际参数、原始响应和时间；后续分页遇到授权或限流时，已有结果仍可使用。',
        )}
      </p>
      <ErrorNotice message={op.error} />
      {notice && (
        <p className="studio-note" role="status">
          {notice}
        </p>
      )}
      <div className="studio-actions">
        <Action secondary onClick={() => setHistoryOpen(!historyOpen)}>
          <History size={13} />
          {t('检索历史 ·')}
          {history.length}
        </Action>
        {found && (
          <Action
            secondary
            disabled={op.busy}
            onClick={() =>
              op.run(async () => setGraph(await studio(workspace, `/discover/${found.id}/graph`)))
            }
          >
            {t('查看本次引用关系')}
          </Action>
        )}
      </div>
      <SubscriptionsPanel
        workspace={workspace}
        search={query}
        providers={providers}
        order={order}
        yearFrom={from ? Number(from) : null}
        yearTo={to ? Number(to) : null}
        openResult={async (id) => {
          setFound(await studio(workspace, `/record/${id}`))
          setGraph(null)
        }}
      />
      {historyOpen && (
        <div className="studio-source-picker">
          {history.map((row) => (
            <button
              className="studio-outline-row"
              key={row.id}
              onClick={() => {
                setFound(row)
                setGraph(null)
                setHistoryOpen(false)
              }}
            >
              <span>{row.data.query}</span>
              <small>
                {new Date(row.created * 1000).toLocaleString()} · {row.data.pages.length}{' '}
                {t('页响应 ·')} {statusText[row.data.status]}
              </small>
            </button>
          ))}
          {!history.length && <p className="studio-note">{t('还没有保存的检索。')}</p>}
        </div>
      )}
      {graph && (
        <div className="studio-panel">
          <h3>{t('本次结果中的引用关系')}</h3>
          <p className="studio-note">
            {graph.nodes.length} {t('篇题录 ·')}
            {graph.edges.length} {t('条已返回的引用。缺少边不代表论文之间没有引用。')}
          </p>
          {graph.edges.map((edge, i) => (
            <p className="studio-citation-edge" key={i}>
              <span>{graph.nodes.find((n) => n.id === edge.source)?.title}</span>
              <b aria-label={t('引用')}>→</b>
              <span>{graph.nodes.find((n) => n.id === edge.target)?.title}</span>
            </p>
          ))}
        </div>
      )}
      {found && (
        <p className="studio-note" style={{ marginTop: 20 }}>
          {t('当前快照：')}
          {found.data.query} · {new Date(found.created * 1000).toLocaleString()}
        </p>
      )}
      {latestPages.map((last) => {
        const pages = found!.data.pages.filter((p) => p.provider === last.provider)
        const items = [
          ...new Map(pages.flatMap((p) => p.items).map((item) => [item.id, item])).values(),
        ]
        return (
          <section key={last.provider}>
            <h3 className="studio-section-title">
              {last.provider}{' '}
              <span className="studio-badge">{statusText[last.status] || last.status}</span>{' '}
              <small>
                {items.length} {t('条 ·')}
                {pages.length} {t('页响应')}
              </small>
            </h3>
            {last.coverage && (
              <p className="studio-note">
                {t('本次检索最多覆盖')}
                {last.coverage.maximum.toLocaleString()} {t('条排序结果。')}
                {last.coverage.limited ? '已到覆盖上限，可缩小年份或检索范围后继续。' : ''}
                {!last.coverage.snapshot_stable
                  ? '来源更新可能影响后续页顺序；每页原始响应均已保存。'
                  : ''}
              </p>
            )}
            {last.status === 'rate_limited' && (
              <p className="studio-note">
                {t('服务要求稍后重试')}
                {last.retry_after ? `（Retry-After: ${last.retry_after}）` : ''}
                {t('。这次请求未被记作零结果。')}
              </p>
            )}
            {last.status === 'authorization_required' && (
              <p className="studio-note">
                {t('数据源要求授权。已有题录已保留，可以使用其他来源继续检索。')}
              </p>
            )}
            {last.status === 'complete' && !items.length && (
              <p className="studio-note">{t('该来源成功返回，但本次检索没有匹配结果。')}</p>
            )}
            {items.map((item) => (
              <article className="studio-card" key={item.id} style={{ marginBottom: 10 }}>
                <h3>{item.title}</h3>
                <p className="studio-muted">
                  {item.authors.slice(0, 3).join(' · ')} · {item.year || '年份未知'}
                </p>
                <footer>
                  {safeUrl(item.url) && (
                    <a
                      className="studio-outlink"
                      href={safeUrl(item.url)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {t('查看来源')}
                      <ArrowUpRight size={13} />
                    </a>
                  )}
                  <Action
                    secondary
                    disabled={op.busy}
                    onClick={() => op.run(() => collect(last.provider, item.id))}
                  >
                    {t('收藏题录')}
                  </Action>
                </footer>
              </article>
            ))}
            {last.provider !== 'rss' && last.status !== 'complete' && (
              <Action
                secondary
                disabled={op.busy}
                onClick={() => op.run(() => next(last.provider))}
              >
                {last.status === 'partial' ? '读取下一页' : '重试这一页'}
              </Action>
            )}
            <details className="studio-note" style={{ margin: '12px 0 24px' }}>
              <summary>{t('查看实际查询与时间')}</summary>
              {pages.map((p, i) => (
                <pre className="studio-diff" key={i}>
                  {JSON.stringify(
                    {
                      provider: p.provider,
                      started: p.started,
                      parameters: p.query,
                      status: p.status,
                      error: p.error,
                    },
                    null,
                    2,
                  )}
                </pre>
              ))}
            </details>
          </section>
        )
      })}
    </section>
  )
}
