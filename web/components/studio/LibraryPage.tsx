'use client'
import { useKeyedState } from '@/hooks/useKeyedState'
import { useTranslation } from 'react-i18next'
import { FileInput } from '@/shared/ui/FileInput'
import IntegrationSources from './IntegrationSources'
import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import DiscoveryPanel from './DiscoveryPanel'
import BibliographyExport from './BibliographyExport'
import StudioLoading from './StudioLoading'
import { BookOpen, Plus, Search, Upload, Compass, ArrowUpRight, Library } from 'lucide-react'
import { Dialog } from '@/shared/ui/Dialog'
import { useActiveWorkspaceId } from '@/hooks/useActiveWorkspaceId'
import { useRecoveryPrincipal } from '@/hooks/useRecoveryPrincipal'
import { activeWorkspaceId, scopedUrl } from '@/lib/workspace-scope'
import { recoveryPrincipal } from '@/lib/recovery-principal'
import {
  post,
  studio,
  StudioRequestError,
  type Work,
  type Edition,
} from '@/lib/studio-api'
import { StudioShell, StudioEmpty, Action, Field, ErrorNotice, useOperation } from './StudioShell'

export default function LibraryPage() {
  const workspace = useActiveWorkspaceId()
  const principal = useRecoveryPrincipal()
  if (!principal) return <StudioLoading label="正在打开资料库…" />
  return <LibraryHome key={JSON.stringify([principal, workspace])} workspace={workspace} principal={principal} />
}
function LibraryHome({ workspace, principal }: { workspace: string; principal: string }) {
  const { t } = useTranslation()
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])
  const isCurrent = () => mounted.current && principal === recoveryPrincipal() && workspace === activeWorkspaceId()

  const [query, setQuery] = useState(''),
    [filter, setFilter] = useState('all')
  const queryKey = JSON.stringify([workspace, query, filter])
  const [works, setWorks] = useKeyedState<Work[]>(queryKey, [])
  const [cursor, setCursor] = useKeyedState(queryKey, '')
  const [loading, setLoading] = useKeyedState(queryKey, true)
  const [listError, setListError] = useKeyedState(queryKey, '')
  const querySequence = useRef(0)
  const collection = useRef<{
    request_id: string
    work?: { title: string; authors: string[]; year: number | null }
    edition: { work_id: string; label: string; doi: string; arxiv: string }
  } | null>(null)
  const [collectionPending, setCollectionPending] = useState(false)
  const [collected, setCollected] = useState<Edition | null>(null)
  const [dialog, setDialog] = useState<'add' | 'import' | 'discover' | 'attach' | 'edition' | null>(
      null,
    ),
    [selected, setSelected] = useState<Edition | null>(null),
    [parent, setParent] = useState<Work | null>(null)
  const [title, setTitle] = useState(''),
    [authors, setAuthors] = useState(''),
    [year, setYear] = useState(''),
    [doi, setDoi] = useState(''),
    [arxiv, setArxiv] = useState(''),
    [label, setLabel] = useState(t('原始版本')),
    [file, setFile] = useState<File | null>(null)
  const [bibliography, setBibliography] = useState(''),
    [format, setFormat] = useState('bibtex'),
    [notice, setNotice] = useState('')
  const op = useOperation()
  async function reload(signal?: AbortSignal, after = '') {
    if (!isCurrent()) return
    const request = ++querySequence.current
    const params = new URLSearchParams({
      q: query,
      project_only: String(filter === 'project'),
      missing: String(filter === 'missing'),
      cursor: after,
    })
    const r = await studio<{ works: Work[]; next_cursor: string }>(
      workspace,
      `/library?${params}`,
      {},
      signal,
    )
    if (!isCurrent() || signal?.aborted || request !== querySequence.current) return
    setWorks((previous) => (after ? [...previous, ...r.works] : r.works))
    setCursor(r.next_cursor)
    setLoading(false)
    setListError('')
  }
  async function refresh() {
    if (!isCurrent()) return
    setLoading(true)
    try {
      await reload()
    } catch (e) {
      setListError(e instanceof Error ? e.message : String(e))
      setLoading(false)
    }
  }
  useEffect(() => {
    const abort = new AbortController()
    const timer = setTimeout(
      () =>
        void reload(abort.signal).catch((e) => {
          if (!abort.signal.aborted) {
            setListError(e.message)
            setLoading(false)
          }
        }),
      200,
    )
    return () => {
      clearTimeout(timer)
      abort.abort()
    }
  }, [workspace, query, filter]) // eslint-disable-line react-hooks/exhaustive-deps
  function open(mode: typeof dialog) {
    setDialog(mode)
    setTitle('')
    setAuthors('')
    setYear('')
    setDoi('')
    setArxiv('')
    setLabel(t('原始版本'))
    setFile(null)
    collection.current = null
    setCollectionPending(false)
    setCollected(null)
    setNotice('')
    op.setError('')
  }
  function close() {
    setDialog(null)
    if (collectionPending) void refresh()
  }
  async function upload(e: Edition, f: File) {
    const data = new FormData()
    data.append('file', f)
    data.append('expected_revision', String(e.revision))
    return studio<Edition>(workspace, `/editions/${e.id}/pdf`, { method: 'POST', body: data })
  }
  async function add() {
    if (!isCurrent()) return
    if (!collection.current) {
      collection.current = {
        request_id: crypto.randomUUID(),
        ...(dialog === 'edition' ? {} : {
          work: {
            title: title.trim() || file?.name.replace(/\.pdf$/i, '') || '',
            authors: authors.split(/;|；/).map(v => v.trim()).filter(Boolean),
            year: year ? Number(year) : null,
          },
        }),
        edition: { work_id: dialog === 'edition' ? parent?.id || '' : '', label, doi, arxiv },
      }
      setCollectionPending(true)
    }
    let edition = collected
    if (!edition) {
      try {
        edition = await post<Edition>(workspace, '/library/collect', collection.current)
        if (!isCurrent()) return
        setCollected(edition)
      } catch (error) {
        // A rejected command did not commit. Transport failures keep the same
        // request identity so a retry recovers a possibly committed response.
        if (error instanceof StudioRequestError && [409, 422].includes(error.status)) {
          collection.current = null
          setCollectionPending(false)
        }
        throw error
      }
    }
    if (!isCurrent()) return
    if (file) await upload(edition, file)
    if (!isCurrent()) return
    setDialog(null)
    setNotice(t('资料已保存。'))
    await refresh()
  }
  const list = works
  const originals = works.flatMap((w) => w.editions).filter((e) => e.data.files.length).length
  return (
    <StudioShell
      workspace={workspace}
      active="library"
      title={t('资料库')}
      description={t("收藏论文和讲义，导入书目与原文，管理文献版本。")}
      action={
        <div className="studio-actions">
          <Action secondary onClick={() => open('discover')}>
            <Compass size={15} />
            {t('发现文献')}
          </Action>
          <Action onClick={() => open('add')}>
            <Plus size={15} />
            {t('收藏资料')}
          </Action>
        </div>
      }
    >
      <IntegrationSources workspace={workspace} provider="zotero" />
      <div className="studio-toolbar">
        <label className="studio-search">
          <Search size={16} />
          <input
            aria-label={t('搜索资料')}
            placeholder={t('标题、作者、DOI、引用键或科学符号')}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <div className="studio-pills">
          {[
            ['all', t('全部资料')],
            ['project', t('当前项目')],
            ['missing', t('待补全文')],
          ].map(([id, text]) => (
            <button key={id} aria-pressed={filter === id} onClick={() => setFilter(id)}>
              {text}
            </button>
          ))}
        </div>
      </div>
      <ErrorNotice message={dialog ? '' : op.error} />
      {listError && (
        <div className="studio-actions">
          <ErrorNotice message={listError} />
          <Action secondary disabled={loading} onClick={() => void refresh()}>{t('Retry')}</Action>
        </div>
      )}
      {notice && !dialog && (
        <p role="status" className="studio-note">
          {notice}
        </p>
      )}
      <div className="studio-grid">
        <main>
          {loading ? (
            <p role="status" className="studio-note">
              {t('正在打开资料库…')}
            </p>
          ) : list.length ? (
            <div className="studio-library-list">
              {list.map((w) => (
                <article className="studio-work" key={w.id}>
                  <div className="studio-work-icon">
                    <BookOpen size={18} />
                  </div>
                  <div>
                    <h2>{w.data.title}</h2>
                    <p>
                      {w.data.authors.slice(0, 4).join(' · ') || t('作者待补充')}
                      {w.data.year ? ` · ${w.data.year}` : ''}
                    </p>
                    <div className="studio-badges">
                      {w.editions.map((e) => (
                        <span
                          className={`studio-badge ${e.data.files.length ? 'good' : ''}`}
                          key={e.id}
                        >
                          {e.data.label} · {e.data.files.length ? t('已存原文') : t('待补全文')}
                          {e.project_role ? t(' · 本项目') : ''}
                        </span>
                      ))}
                      {w.data.tags.map((tag) => (
                        <span className="studio-badge" key={tag}>
                          {tag}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className="studio-work-tools">
                    {w.editions.map((e) => (
                      <div className="studio-actions" key={e.id}>
                        {e.data.files.length ? (
                          <Link
                            className="studio-outlink"
                            href={scopedUrl(`/library/${e.id}`, workspace)}
                          >
                            {t('阅读')}
                            {w.editions.length > 1 ? ` · ${e.data.label}` : ''}
                            <ArrowUpRight size={13} />
                          </Link>
                        ) : (
                          <button
                            className="studio-outlink"
                            onClick={() => {
                              open('attach')
                              setSelected(e)
                            }}
                          >
                            {t('添加原文')}
                            <Upload size={13} />
                          </button>
                        )}
                        {!!e.data.bibliographic_sources?.length && (
                          <details className="studio-note">
                            <summary>
                              {t('导入来源')}
                              {e.data.bibliographic_sources.length} {t('· 字段冲突')}{' '}
                              {e.data.bibliographic_conflicts?.length || 0}
                            </summary>
                            <p>
                              {t('引用键：')}
                              {e.data.citekey_aliases?.join('、') || e.data.citekey}
                            </p>
                            {e.data.bibliographic_conflicts?.map((c, i) => (
                              <p key={i}>
                                {c.field}
                                {t('：保留')}
                                {String(c.retained)}
                                {t('；新来件')}
                                {String(c.incoming)}
                              </p>
                            ))}
                            <p>{t('原始来件均已保留；冲突字段不会自动覆盖。')}</p>
                          </details>
                        )}
                        {!e.project_role && (
                          <button
                            className="studio-outlink"
                            disabled={op.busy}
                            onClick={() =>
                              void op.run(async () => {
                                if (!isCurrent()) return
                                await post(workspace, `/editions/${e.id}/project`, {
                                  role: 'reading',
                                })
                                await reload()
                              })
                            }
                          >
                            {t('加入项目')}
                          </button>
                        )}
                      </div>
                    ))}
                    <button
                      className="studio-outlink"
                      onClick={() => {
                        open('edition')
                        setParent(w)
                      }}
                    >
                      {t('添加另一版本')}
                    </button>
                  </div>
                </article>
              ))}
            </div>
          ) : !listError ? (
            <StudioEmpty title={query || filter !== 'all' ? t('没有匹配的资料') : t('从一份想读懂的资料开始')}>
              {query || filter !== 'all' ? t('可以换个关键词，或查看全部资料。') : t(
                '收藏 PDF 或题录，留下疑问，再把原文里的线索整理成自己的理解。已有阅读资料仍可从右侧打开。',
              )}
            </StudioEmpty>
          ) : null}
          {!loading && cursor && (
            <Action
              secondary
              disabled={op.busy}
              onClick={() => op.run(() => reload(undefined, cursor))}
            >
              {t('加载更多资料')}
            </Action>
          )}
        </main>
        <aside className="studio-aside">
          <section className="studio-panel">
            <Library size={19} />
            <div className="studio-stat">{works.length}</div>
            <h2>{t('当前列表中的资料')}</h2>
            <p>
              {originals}{' '}
              {t('个版本已保存原文。题录和原件在个人资料库中共享，项目里的筛选与证据独立保存。')}
            </p>
          </section>
          <section className="studio-panel">
            <h2>{t('把已有积累带进来')}</h2>
            <p>
              {t('支持 BibTeX、RIS、CSL-JSON。保留已有引用键，精确标识去重，相似标题留给你判断。')}
            </p>
            <div className="studio-actions" style={{ marginTop: 17 }}>
              <Action secondary onClick={() => open('import')}>
                {t('导入书目')}
              </Action>
              <BibliographyExport workspace={workspace} works={works} />
            </div>
            <p className="studio-note">
              <Link className="studio-outlink" href={scopedUrl('/learning/reading', workspace)}>
                {t('打开已有阅读资料')}
                <ArrowUpRight size={13} />
              </Link>
            </p>
          </section>
        </aside>
      </div>
      <Dialog
        open={dialog !== null}
        title={
          dialog === 'discover'
            ? t('从一个问题，发现下一篇论文')
            : dialog === 'import'
              ? t('导入已有书目')
              : dialog === 'attach'
                ? t('保存这个版本的原文')
                : dialog === 'edition'
                  ? t('添加可独立引用的版本')
                  : t('收藏一份资料')
        }
        onClose={close}
        size={dialog === 'discover' ? 'xl' : 'lg'}
        busy={op.busy}
        className="studio-dialog"
        footer={
          dialog === 'discover' ? undefined : (
            <div className="studio-actions">
              <Action secondary onClick={close} disabled={op.busy}>
                {t('取消')}
              </Action>
              <Action
                disabled={op.busy || (dialog === 'attach' && !file) ||
                  (dialog === 'add' && !collectionPending && !title.trim() && !file) ||
                  (dialog === 'import' && !bibliography.trim())}
                onClick={() =>
                  void op.run(async () => {
                    if (!isCurrent()) return
                    if (dialog === 'import') {
                      const result = await post<{
                        entries: {
                          status: string
                          detail?: string
                          enriched?: boolean
                          conflicts?: number
                        }[]
                      }>(workspace, '/bibliography/import', { text: bibliography, format })
                      await refresh()
                      const errors = result.entries.filter((e) => e.status === 'error')
                      setNotice(
                        t("已处理 {{value1}} 条；补全 {{value2}} 条；{{value3}} 项字段冲突已保留；{{value4}} 条需要检查。{{value5}}", { value1: result.entries.length, value2: result.entries.filter((e) => e.enriched).length, value3: result.entries.reduce((n, e) => n + (e.conflicts || 0), 0), value4: errors.length, value5: errors.map((e) => e.detail).join('；') }),
                      )
                      if (!errors.length) setDialog(null)
                    } else if (dialog === 'attach' && selected && file) {
                      await upload(selected, file)
                      setDialog(null)
                      await refresh()
                    } else await add()
                  })
                }
              >
                {op.busy ? t('正在保存…') : collectionPending ? t('继续保存') : t('保存到资料库')}
              </Action>
            </div>
          )
        }
      >
        <ErrorNotice message={op.error} />
        {collectionPending && !op.busy && (
          <p className="studio-note" role="status">
            {collected
              ? t('题录已保存，原文尚未完成。可重试上传，或关闭后从“添加原文”继续。')
              : t('正在确认这次收藏的结果。重试会接续同一次保存。')}
          </p>
        )}
        {notice && (
          <p className="studio-note" role="status">
            {notice}
          </p>
        )}
        {dialog === 'add' || dialog === 'edition' ? (
          <div className="studio-form">
            <fieldset disabled={op.busy || collectionPending} className="studio-form min-w-0">
            {dialog === 'add' && (
              <>
                <Field label={t("标题")}>
                  <input
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    placeholder={t('也可以先选择 PDF')}
                  />
                </Field>
                <div className="studio-form-grid">
                  <Field label={t("作者（用分号分隔）")}>
                    <input value={authors} onChange={(e) => setAuthors(e.target.value)} />
                  </Field>
                  <Field label={t("年份")}>
                    <input type="number" value={year} onChange={(e) => setYear(e.target.value)} />
                  </Field>
                </div>
              </>
            )}
            <Field label={t("版本名称")} hint={t("例如 arXiv v1、会议正式版；版本不会互相覆盖。")}>
              <input value={label} onChange={(e) => setLabel(e.target.value)} />
            </Field>
            <div className="studio-form-grid">
              <Field label={t("DOI（可选）")}>
                <input value={doi} onChange={(e) => setDoi(e.target.value)} />
              </Field>
              <Field label={t("arXiv ID（可选）")}>
                <input
                  value={arxiv}
                  onChange={(e) => setArxiv(e.target.value)}
                  placeholder={t('包含 v1 / v2 可精确区分版本')}
                />
              </Field>
            </div>
            </fieldset>
            <label className="studio-upload">
              <Upload size={23} />
              <span>{t('保存 PDF 原文，或稍后再补')}</span>
              <FileInput
                aria-label={t('PDF 原文')}
                disabled={op.busy}
                accept="application/pdf"
                onChange={(e) => setFile(e.target.files?.[0] || null)}
              />
            </label>
          </div>
        ) : null}
        {dialog === 'attach' && (
          <label className="studio-upload">
            <Upload size={23} />
            <span>{t('原件按内容保存，同一文件更名不会重复入库。')}</span>
            <FileInput
                aria-label={t('PDF 原文')}
                disabled={op.busy}
                accept="application/pdf"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
          </label>
        )}
        {dialog === 'import' && (
          <div className="studio-form">
            <Field label={t("书目格式")}>
              <select value={format} onChange={(e) => setFormat(e.target.value)}>
                <option value="bibtex">{t('BibTeX / BibLaTeX')}</option>
                <option value="ris">{t('RIS')}</option>
                <option value="csl">{t('CSL-JSON')}</option>
              </select>
            </Field>
            <FileInput
              aria-label={t('选择书目文件')}
                accept=".bib,.ris,.json"
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) void f.text().then(setBibliography)
              }}
            />
            <Field label={t("书目内容")}>
              <textarea
                rows={12}
                value={bibliography}
                onChange={(e) => setBibliography(e.target.value)}
              />
            </Field>
          </div>
        )}
        {dialog === 'discover' && <DiscoveryPanel workspace={workspace} onCollected={reload} />}
      </Dialog>
    </StudioShell>
  )
}
