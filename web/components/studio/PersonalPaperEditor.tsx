'use client'
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  GitBranch,
  ListTree,
  MessageSquareText,
  PanelLeft,
  Plus,
  Save,
  Search,
  X,
} from 'lucide-react'
import dynamic from 'next/dynamic'
import Image from 'next/image'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Dialog } from '@/shared/ui/Dialog'
import { Tooltip } from '@/shared/ui/Tooltip'
import { studio, studioUrl } from '@/lib/studio-api'
import { buildDiagnostics } from '@/lib/latexcoder/compile-errors'
import { latexDiagnostics } from '@/lib/latexcoder/latex-diagnostics'
import {
  flattenStructure,
  projectStructure,
  type StructureHeading,
} from '@/lib/latexcoder/structure'
import { normalizeProjectPath } from '@/lib/latexcoder/assets'
import { stripReviewStorage } from '@/lib/latexcoder/review'
import { useOperation, Action, ErrorNotice, Field } from './StudioShell'
import type { PaperFile, PaperIndex } from './paper-navigation'
import type { TextSelection } from './TexEditor'
import type { ProjectPanel } from './PaperProjectTools'
const TexEditor = dynamic(() => import('./TexEditor'), { ssr: false })
const PaperProjectTools = dynamic(() => import('./PaperProjectTools'))
const PaperNotes = dynamic(() => import('./PaperNotes'))

type Props = {
  workspace: string
  manuscript: string
  main: string
  files: PaperFile[]
  file: { path: string; hash: string } | null
  text: string
  status: string
  saved: boolean
  log: string
  selection: TextSelection
  jump?: { line: number; nonce: number }
  onSelect: (range: TextSelection) => void
  onChange: (text: string) => void
  onOpen: (path: string, line?: number) => Promise<void>
  flush: () => Promise<void>
  refresh: (preferred?: string) => Promise<void>
  add: () => void
}

export default function PersonalPaperEditor(props: Props) {
  const { t } = useTranslation()
  const { workspace, manuscript, file, files, text, main, flush, refresh, onOpen } = props
  const path = file?.path || '',
    base = '/manuscripts/' + manuscript
  const op = useOperation()
  const [snapshot, setSnapshot] = useState<PaperIndex | null>(null),
    [epoch, setEpoch] = useState(0)
  const [panel, setPanel] = useState<ProjectPanel | 'notes' | 'checks' | null>(null)
  const [tab, setTab] = useState<'files' | 'outline'>('files'),
    [side, setSide] = useState(true)
  const [tabs, setTabs] = useState<string[]>([]),
    [folded, setFolded] = useState<string[]>([])
  const [asset, setAsset] = useState(''),
    [markdown, setMarkdown] = useState(false)
  const [dragged, setDragged] = useState<PaperFile | null>(null)
  const [summary, setSummary] = useState<{
    entry: StructureHeading
    source: string
    hash: string
    value: string
  } | null>(null)
  const setError = op.setError
  useEffect(() => {
    const abort = new AbortController()
    void studio<PaperIndex>(workspace, base + '/editor', {}, abort.signal)
      .then(setSnapshot)
      .catch(e => {
        if (!abort.signal.aborted) setError(e.message)
      })
    return () => abort.abort()
  }, [workspace, base, epoch, path, file?.hash, setError])
  // Retain tabs for this paper while the author moves between references. The open path
  // is authoritative, including after a jump from a citation or a build diagnostic.
  const openTabs = [...new Set([...tabs, path])].filter(p => files.some(f => f.path === p))
  const index = useMemo(
    () => snapshot || { sources: {}, files, omitted: [], main },
    [snapshot, files, main]
  )
  const assetUrl = (name: string) =>
    studioUrl(base + '/asset?path=' + encodeURIComponent(name), workspace)
  async function navigate(name: string, line?: number) {
    const next = files.find(f => f.path === name)
    if (!next) return
    if (!next.editable) {
      setAsset(name)
      return
    }
    await onOpen(name, line)
    setTabs([...new Set([...openTabs, name])])
    setMarkdown(false)
  }
  async function reindex(preferred?: string) {
    await refresh(preferred)
    setEpoch(v => v + 1)
  }
  const sources = useMemo(
    () => new Map(Object.entries({ ...index.sources, ...(path ? { [path]: text } : {}) })),
    [index.sources, path, text]
  )
  const outline = useMemo(
    () => (tab === 'outline' ? flattenStructure(projectStructure(main, sources)) : []),
    [tab, main, sources]
  )
  const tree = useMemo(() => {
    const rows: { key: string; label: string; folder: boolean; depth: number }[] = [],
      seen = new Set<string>()
    for (const f of [...files].sort((a, b) => a.path.localeCompare(b.path))) {
      const parts = f.path.split('/')
      for (let i = 0; i < parts.length; i++) {
        const name = parts.slice(0, i + 1).join('/'),
          folder = i < parts.length - 1
        if (!seen.has(name)) {
          rows.push({ key: name, label: parts[i], folder, depth: i })
          seen.add(name)
        }
        if (folder && folded.includes(name)) break
      }
    }
    return rows
  }, [files, folded])
  const diagnostics = useMemo(
    () =>
      panel === 'checks'
        ? [
            ...latexDiagnostics([...sources].map(([path, source]) => ({ path, source }))),
            ...buildDiagnostics(props.log, undefined, main),
          ]
        : [],
    [sources, panel, props.log, main]
  )
  const choosePanel = (value: typeof panel) =>
    op.run(async () => {
      await flush()
      setPanel(value)
    })
  const relative = (value: string) =>
    normalizeProjectPath(path.split('/').slice(0, -1).concat(value).join('/'))
  return (
    <section className="writer-editor" aria-label={t('论文源文件')}>
      <div className="writer-commandbar">
        <Tooltip label={t('文件与大纲')}>
          <button
            type="button"
            aria-label={t('文件与大纲')}
            aria-expanded={side}
            onClick={() => setSide(!side)}
          >
            <PanelLeft size={15} />
          </button>
        </Tooltip>
        <button type="button" onClick={() => choosePanel('search')}>
          <Search size={14} />
          <span>{t('查找')}</span>
        </button>
        <button type="button" onClick={() => choosePanel('history')}>
          <GitBranch size={14} />
          <span>{t('版本')}</span>
        </button>
        <button
          type="button"
          disabled={!/\.(tex|md)$/i.test(path)}
          onClick={() => choosePanel('notes')}
        >
          <MessageSquareText size={14} />
          <span>{t('批注')}</span>
        </button>
        <button type="button" onClick={() => choosePanel('files')}>
          <FolderPlus size={14} />
          <span>{t('工程文件')}</span>
        </button>
        <Tooltip label={t('保存源文件')} className="writer-save">
          <button
            type="button"
            aria-label={t('保存源文件')}
            className="writer-save"
            onClick={() => op.run(flush)}
          >
            <Save size={14} />
            <span role="status">{props.status}</span>
          </button>
        </Tooltip>
      </div>
      <div className="writer-body" data-sidebar={side}>
        {side && (
          <aside className="writer-side">
            <div className="writer-switch">
              <button aria-pressed={tab === 'files'} onClick={() => setTab('files')}>
                {t('文件')}
              </button>
              <button aria-pressed={tab === 'outline'} onClick={() => setTab('outline')}>
                {t('大纲')}
              </button>
            </div>
            {tab === 'files' ? (
              <>
                <div
                  className="writer-file-list"
                  onDragOver={e => {
                    if (dragged) e.preventDefault()
                  }}
                  onDrop={e => {
                    e.preventDefault()
                    if (!dragged || op.busy) return
                    const folder =
                      (e.target as Element).closest<HTMLElement>('[data-folder]')?.dataset.folder ||
                      ''
                    const destination = [folder, dragged.path.split('/').at(-1)]
                      .filter(Boolean)
                      .join('/')
                    const original = dragged
                    setDragged(null)
                    if (destination === original.path) return
                    void op.run(async () => {
                      await flush()
                      const saved = original.path === file?.path ? file.hash : original.hash
                      await studio(workspace, base + '/files/change', {
                        method: 'POST',
                        body: JSON.stringify({
                          path: original.path,
                          base_hash: saved,
                          destination,
                        }),
                      })
                      await reindex(original.path === file?.path ? destination : undefined)
                    })
                  }}
                >
                  {tree.map(row => (
                    <Tooltip key={row.key} label={row.key} className="writer-file-hint">
                      <button
                        data-folder={
                          row.folder ? row.key : row.key.split('/').slice(0, -1).join('/')
                        }
                        draggable={!row.folder && row.key !== main && props.saved}
                        onDragStart={e => {
                          const source = files.find(f => f.path === row.key)
                          if (!source || row.folder) {
                            e.preventDefault()
                            return
                          }
                          e.dataTransfer.effectAllowed = 'move'
                          e.dataTransfer.setData('text/plain', row.key)
                          setDragged(source)
                        }}
                        onDragEnd={() => setDragged(null)}
                        style={{ paddingInlineStart: 8 + row.depth * 10 }}
                        aria-current={!row.folder && path === row.key ? 'page' : undefined}
                        aria-expanded={row.folder ? !folded.includes(row.key) : undefined}
                        onClick={() =>
                          row.folder
                            ? setFolded(old =>
                                old.includes(row.key)
                                  ? old.filter(p => p !== row.key)
                                  : [...old, row.key]
                              )
                            : op.run(() => navigate(row.key))
                        }
                      >
                        {row.folder ? (
                          folded.includes(row.key) ? (
                            <Folder size={13} />
                          ) : (
                            <FolderOpen size={13} />
                          )
                        ) : (
                          <FileText size={13} />
                        )}
                        <span>{row.label}</span>
                      </button>
                    </Tooltip>
                  ))}
                </div>
                <button className="writer-add" onClick={props.add}>
                  <Plus size={13} />
                  {t('新建文件')}
                </button>
              </>
            ) : (
              <div className="writer-outline">
                {outline.map(entry => (
                  <div key={entry.id} style={{ paddingInlineStart: Math.min(entry.level, 4) * 5 }}>
                    <Tooltip label={entry.path}>
                      <button onClick={() => op.run(() => navigate(entry.path, entry.line))}>
                        {entry.title}
                      </button>
                    </Tooltip>
                    {entry.type === 'heading' && (
                      <>
                        {entry.summary && <small>{entry.summary.title}</small>}
                        <button
                          className="writer-summary-action"
                          aria-label={t('编辑章节要点') + ' · ' + entry.title}
                          onClick={() =>
                            op.run(async () => {
                              await flush()
                              const fresh = await studio<{ text: string; hash: string }>(
                                workspace,
                                base + '/file?path=' + encodeURIComponent(entry.path)
                              )
                              if (fresh.text !== sources.get(entry.path))
                                throw new Error(t('稿件已变化，请重新打开大纲。'))
                              setSummary({
                                entry,
                                source: fresh.text,
                                hash: fresh.hash,
                                value: entry.summary
                                  ? fresh.text.slice(
                                      entry.summary.range.from,
                                      entry.summary.range.to
                                    )
                                  : '',
                              })
                            })
                          }
                        >
                          <ListTree size={13} />
                        </button>
                      </>
                    )}
                  </div>
                ))}
                {!outline.length && <small>{t('添加章节后，大纲会出现在这里。')}</small>}
              </div>
            )}
          </aside>
        )}
        <div className="writer-canvas">
          <div className="writer-tabs">
            {openTabs.map(name => (
              <span key={name} data-active={name === path}>
                <Tooltip label={name}>
                  <button onClick={() => op.run(() => navigate(name))}>
                    {name.split('/').at(-1)}
                  </button>
                </Tooltip>
                {openTabs.length > 1 && (
                  <button
                    aria-label={t('关闭文件') + ' · ' + name}
                    onClick={() =>
                      op.run(async () => {
                        const rest = openTabs.filter(p => p !== name)
                        if (path === name) await onOpen(rest.at(-1)!)
                        setTabs(rest)
                      })
                    }
                  >
                    <X size={11} />
                  </button>
                )}
              </span>
            ))}
          </div>
          {/\.md$/i.test(path) && (
            <div className="writer-switch">
              <button aria-pressed={!markdown} onClick={() => setMarkdown(false)}>
                {t('源码')}
              </button>
              <button aria-pressed={markdown} onClick={() => setMarkdown(true)}>
                {t('预览')}
              </button>
            </div>
          )}
          {/\.svg$/i.test(path) && (
            <button
              className="studio-link-button"
              onClick={() =>
                op.run(async () => {
                  await flush()
                  setAsset(path)
                })
              }
            >
              {t('预览')}
            </button>
          )}
          {file &&
            (markdown && /\.md$/i.test(path) ? (
              <article className="writer-markdown prose dark:prose-invert">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  skipHtml
                  components={{
                    img: ({ src, alt }) => {
                      const name = typeof src === 'string' ? relative(src) : ''
                      return files.some(f => f.path === name) &&
                        /\.(png|jpe?g|svg)$/i.test(name) ? (
                        <Image
                          unoptimized
                          width={640}
                          height={480}
                          style={{ width: 'auto', height: 'auto' }}
                          src={assetUrl(name)}
                          alt={alt || name}
                        />
                      ) : (
                        <span>{alt}</span>
                      )
                    },
                    a: ({ href, children }) => {
                      const name = href ? relative(href) : ''
                      return files.some(f => f.path === name) ? (
                        <button onClick={() => op.run(() => navigate(name))}>{children}</button>
                      ) : (
                        <a
                          href={href && /^https?:\/\//i.test(href) ? href : undefined}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          {children}
                        </a>
                      )
                    },
                  }}
                >
                  {stripReviewStorage(text)}
                </ReactMarkdown>
              </article>
            ) : (
              <TexEditor
                identity={manuscript + ':' + path}
                value={text}
                onChange={props.onChange}
                onSelection={props.onSelect}
                jump={props.jump}
                navigation={{
                  index,
                  path,
                  asset: assetUrl,
                  open: (name, line) => void op.run(() => navigate(name, line)),
                }}
              />
            ))}
          <div className="writer-status">
            <button onClick={() => choosePanel('checks')}>{t('引用与编译诊断')}</button>
            <span>
              {t('第 {{line}} 行', {
                line: Array.from(text).slice(0, props.selection.start).join('').split('\n').length,
              })}
            </span>
          </div>
        </div>
      </div>
      <ErrorNotice message={op.error} />
      {index.omitted.length > 0 && (
        <p className="studio-note">
          {t('有 {{count}} 个文件超过编辑器索引范围。整篇搜索仍会检查它们。', {
            count: index.omitted.length,
          })}
        </p>
      )}
      {panel && ['files', 'history', 'search'].includes(panel) && (
        <PaperProjectTools
          key={panel}
          panel={panel as ProjectPanel}
          workspace={workspace}
          manuscript={manuscript}
          file={
            files.find(f => f.path === path) && {
              ...files.find(f => f.path === path)!,
              hash: file!.hash,
            }
          }
          main={main}
          flush={flush}
          changed={reindex}
          navigate={navigate}
          onClose={() => setPanel(null)}
        />
      )}
      {panel === 'notes' && (
        <PaperNotes
          path={path}
          text={text}
          selection={props.selection}
          save={props.onChange}
          close={() => setPanel(null)}
        />
      )}
      <Dialog
        open={panel === 'checks'}
        onClose={() => setPanel(null)}
        title={t('引用与编译诊断')}
        size="md"
        className="studio-dialog"
      >
        {!diagnostics.length && <p>{t('当前索引和编译日志里没有发现问题。')}</p>}
        <ul className="writer-rows">
          {diagnostics.map((item, i) => {
            const split = /^(Duplicate label|Undefined reference|Undefined citation): (.*)$/.exec(
              item.message
            )
            const label = split
              ? (split[1] === 'Duplicate label'
                  ? t('标签重复')
                  : split[1] === 'Undefined citation'
                    ? t('未定义的引用键')
                    : t('未定义的交叉引用')) +
                ' · ' +
                split[2]
              : item.message
            return (
              <li key={i}>
                <button
                  className="writer-row"
                  disabled={!item.path || !files.some(f => f.path === item.path)}
                  onClick={() =>
                    op.run(async () => {
                      await navigate(item.path!, item.line)
                      setPanel(null)
                    })
                  }
                >
                  <span>{label}</span>
                  <small>
                    {item.path} {item.line}
                  </small>
                </button>
              </li>
            )
          })}
        </ul>
      </Dialog>
      <Dialog
        open={!!summary}
        onClose={() => setSummary(null)}
        title={t('编辑章节要点')}
        size="md"
        className="studio-dialog"
      >
        {summary && (
          <>
            <Field label={summary.entry.title}>
              <textarea
                rows={3}
                value={summary.value}
                maxLength={2000}
                onChange={e => setSummary({ ...summary, value: e.target.value })}
              />
            </Field>
            <Action
              onClick={() =>
                op.run(async () => {
                  const { entry, source, hash, value } = summary
                  if (!value.trim() || /[{}\\%]/.test(value))
                    throw new Error(t('请用普通文本写下章节要点。'))
                  const range = entry.summary?.range
                  const at = range?.from ?? entry.commandRange.to
                  const updated =
                    source.slice(0, at) +
                    (range ? value : '\n\\sectiontldr{' + value + '}') +
                    source.slice(range?.to ?? at)
                  await studio(workspace, base + '/file', {
                    method: 'PUT',
                    body: JSON.stringify({ path: entry.path, base_hash: hash, text: updated }),
                  })
                  await reindex(entry.path)
                  setSummary(null)
                })
              }
            >
              {t('保存章节要点')}
            </Action>
            <ErrorNotice message={op.error} />
          </>
        )}
      </Dialog>
      <Dialog
        open={!!asset}
        onClose={() => setAsset('')}
        title={asset}
        size="lg"
        className="studio-dialog"
      >
        {asset && (
          <div className="writer-asset">
            {/\.(png|jpe?g|svg)$/i.test(asset) ? (
              <Image
                unoptimized
                width={900}
                height={680}
                style={{ width: 'auto', height: 'auto' }}
                alt={asset}
                src={assetUrl(asset)}
              />
            ) : /\.pdf$/i.test(asset) ? (
              <iframe title={asset} src={assetUrl(asset)} />
            ) : null}
            <a download href={assetUrl(asset)}>
              {t('下载文件')}
            </a>
          </div>
        )}
      </Dialog>
    </section>
  )
}
