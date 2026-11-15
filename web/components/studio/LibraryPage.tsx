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
import LibraryWorkRow from './LibraryWorkRow'
import { Plus, Search, Compass, BookOpen, Upload } from 'lucide-react'
import { pageActionClass } from '@/components/layout/FeaturePage'
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
import { StudioShell, Action, Field, ErrorNotice, useOperation } from './StudioShell'
import './library.css'

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
    [notice, setNotice] = useKeyedState(queryKey, '')
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
    op.setError('')
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
  return (
    <StudioShell
      workspace={workspace}
      active="library"
      className="library-page"
      title={t('资料库')}
      description=""
      action={
        <div className="studio-actions">
          <button type="button" className={pageActionClass('secondary')} onClick={() => open('discover')}>
            <Compass size={15} />
            {t('发现文献')}
          </button>
          <button type="button" className={pageActionClass()} onClick={() => open('add')}>
            <Plus size={15} />
            {t('添加资料')}
          </button>
        </div>
      }
    >
      <div className="library-toolbar">
        <label className="studio-search library-search">
          <Search size={16} />
          <input
            aria-label={t('搜索资料')}
            placeholder={t('搜索标题、作者或 DOI')}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <div className="library-filters">
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
      <div className="library-list-tools">
        <span className="library-count">{!loading && !listError ? t('已显示 {{count}} 项', { count: works.length }) : ''}</span>
        <div className="library-utilities">
          <button type="button" onClick={() => open('import')}>{t('导入书目')}</button>
          <BibliographyExport workspace={workspace} works={works} />
          <Link href={scopedUrl('/learning/reading', workspace)}>{t('阅读资料')}</Link>
        </div>
      </div>
      <main className="library-content">
        {loading ? (
          <p role="status" className="studio-note">{t('正在打开资料库…')}</p>
        ) : list.length ? (
          <div className="library-list">
            {list.map(work => (
              <LibraryWorkRow
                key={work.id}
                work={work}
                workspace={workspace}
                busy={op.busy}
                onAttach={edition => { open('attach'); setSelected(edition) }}
                onAddEdition={() => { open('edition'); setParent(work) }}
                onJoin={edition => void op.run(async () => {
                  if (!isCurrent()) return
                  await post(workspace, `/editions/${edition.id}/project`, { role: 'reading' })
                  await reload()
                })}
              />
            ))}
          </div>
        ) : !listError ? (
          <div className="library-empty">
            <BookOpen size={24} strokeWidth={1.5} aria-hidden />
            <h2>{query || filter !== 'all' ? t('没有匹配的资料') : t('还没有资料')}</h2>
            <p>{query || filter !== 'all' ? t('试试其他关键词，或查看全部资料。') : t('添加 PDF 或导入书目。')}</p>
            {query || filter !== 'all' ? (
              <button type="button" className={pageActionClass('secondary')} onClick={() => { setQuery(''); setFilter('all') }}>{t('查看全部资料')}</button>
            ) : (
              <button type="button" className={pageActionClass('secondary')} onClick={() => open('add')}><Plus size={14} />{t('添加第一份资料')}</button>
            )}
          </div>
        ) : null}
        {!loading && cursor && (
          <Action secondary disabled={op.busy} onClick={() => op.run(() => reload(undefined, cursor))}>{t('加载更多资料')}</Action>
        )}
      </main>
      <IntegrationSources workspace={workspace} provider="zotero" compact />
      <Dialog
        open={dialog !== null}
        title={
          dialog === 'discover'
            ? t('发现文献')
            : dialog === 'import'
              ? t('导入书目')
              : dialog === 'attach'
                ? t('添加原文')
                : dialog === 'edition'
                  ? t('添加版本')
                  : t('添加资料')
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
