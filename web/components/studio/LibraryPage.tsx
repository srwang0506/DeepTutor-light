'use client'
import { useEffect, useState } from 'react'
import Link from 'next/link'
import DiscoveryPanel from './DiscoveryPanel'
import BibliographyExport from './BibliographyExport'
import { BookOpen, Plus, Search, Upload, Compass, ArrowUpRight, Library } from 'lucide-react'
import { Dialog } from '@/shared/ui/Dialog'
import { useActiveWorkspaceId } from '@/hooks/useActiveWorkspaceId'
import { scopedUrl } from '@/lib/workspace-scope'
import {
  post,
  studio,
  type Work,
  type Edition,
  type StudioRecord,
  type WorkData,
} from '@/lib/studio-api'
import { StudioShell, StudioEmpty, Action, Field, ErrorNotice, useOperation } from './StudioShell'

export default function LibraryPage() {
  const workspace = useActiveWorkspaceId()
  return <LibraryHome key={workspace} workspace={workspace} />
}
function LibraryHome({ workspace }: { workspace: string }) {
  const [works, setWorks] = useState<Work[]>([]),
    [loading, setLoading] = useState(true),
    [cursor, setCursor] = useState(''),
    [query, setQuery] = useState(''),
    [filter, setFilter] = useState('all')
  const [dialog, setDialog] = useState<'add' | 'import' | 'discover' | 'attach' | 'edition' | null>(
      null
    ),
    [selected, setSelected] = useState<Edition | null>(null),
    [parent, setParent] = useState<Work | null>(null)
  const [title, setTitle] = useState(''),
    [authors, setAuthors] = useState(''),
    [year, setYear] = useState(''),
    [doi, setDoi] = useState(''),
    [arxiv, setArxiv] = useState(''),
    [label, setLabel] = useState('原始版本'),
    [file, setFile] = useState<File | null>(null)
  const [bibliography, setBibliography] = useState(''),
    [format, setFormat] = useState('bibtex'),
    [notice, setNotice] = useState('')
  const op = useOperation()
  async function reload(signal?: AbortSignal, after = '') {
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
      signal
    )
    setWorks(previous => (after ? [...previous, ...r.works] : r.works))
    setCursor(r.next_cursor)
    setLoading(false)
  }
  useEffect(() => {
    const abort = new AbortController()
    setLoading(true)
    const timer = setTimeout(
      () =>
        void reload(abort.signal).catch(e => {
          if (!abort.signal.aborted) {
            op.setError(e.message)
            setLoading(false)
          }
        }),
      200
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
    setLabel('原始版本')
    setFile(null)
    setNotice('')
    op.setError('')
  }
  async function upload(e: Edition, f: File) {
    const data = new FormData()
    data.append('file', f)
    data.append('expected_revision', String(e.revision))
    await studio(workspace, `/editions/${e.id}/pdf`, { method: 'POST', body: data })
  }
  async function add() {
    let work = parent as StudioRecord<WorkData> | null
    if (dialog !== 'edition')
      work = await post<StudioRecord<WorkData>>(workspace, '/works', {
        title: title.trim() || file?.name.replace(/\.pdf$/i, '') || '',
        authors: authors
          .split(/;|；/)
          .map(v => v.trim())
          .filter(Boolean),
        year: year ? Number(year) : null,
      })
    if (!work) return
    const e = await post<Edition>(workspace, '/editions', { work_id: work.id, label, doi, arxiv })
    await post(workspace, `/editions/${e.id}/project`, { role: 'reading' })
    try {
      if (file) await upload(e, file)
    } finally {
      await reload()
    }
    setDialog(null)
  }
  const list = works
  const originals = works.flatMap(w => w.editions).filter(e => e.data.files.length).length
  return (
    <StudioShell
      workspace={workspace}
      active="library"
      title="资料，慢慢成为理解。"
      description="把论文、讲义与自己的问题放在一起。原文只收藏一次，版本与证据清楚地留在身边。"
      action={
        <div className="studio-actions">
          <Action secondary onClick={() => open('discover')}>
            <Compass size={15} />
            发现文献
          </Action>
          <Action onClick={() => open('add')}>
            <Plus size={15} />
            收藏资料
          </Action>
        </div>
      }
    >
      <div className="studio-toolbar">
        <label className="studio-search">
          <Search size={16} />
          <input
            aria-label="搜索资料"
            placeholder="标题、作者、DOI、引用键或科学符号"
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
        </label>
        <div className="studio-pills">
          {[
            ['all', '全部资料'],
            ['project', '当前项目'],
            ['missing', '待补全文'],
          ].map(([id, text]) => (
            <button key={id} aria-pressed={filter === id} onClick={() => setFilter(id)}>
              {text}
            </button>
          ))}
        </div>
      </div>
      <ErrorNotice message={dialog ? '' : op.error} />
      {notice && !dialog && (
        <p role="status" className="studio-note">
          {notice}
        </p>
      )}
      <div className="studio-grid">
        <main>
          {loading ? (
            <p role="status" className="studio-note">
              正在打开资料库…
            </p>
          ) : list.length ? (
            <div className="studio-library-list">
              {list.map(w => (
                <article className="studio-work" key={w.id}>
                  <div className="studio-work-icon">
                    <BookOpen size={18} />
                  </div>
                  <div>
                    <h2>{w.data.title}</h2>
                    <p>
                      {w.data.authors.slice(0, 4).join(' · ') || '作者待补充'}
                      {w.data.year ? ` · ${w.data.year}` : ''}
                    </p>
                    <div className="studio-badges">
                      {w.editions.map(e => (
                        <span
                          className={`studio-badge ${e.data.files.length ? 'good' : ''}`}
                          key={e.id}
                        >
                          {e.data.label} · {e.data.files.length ? '已存原文' : '待补全文'}
                          {e.project_role ? ' · 本项目' : ''}
                        </span>
                      ))}
                      {w.data.tags.map(tag => (
                        <span className="studio-badge" key={tag}>
                          {tag}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className="studio-work-tools">
                    {w.editions.map(e => (
                      <div className="studio-actions" key={e.id}>
                        {e.data.files.length ? (
                          <Link
                            className="studio-outlink"
                            href={scopedUrl(`/library/${e.id}`, workspace)}
                          >
                            阅读{w.editions.length > 1 ? ` · ${e.data.label}` : ''}
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
                            添加原文
                            <Upload size={13} />
                          </button>
                        )}
                        {!e.project_role && (
                          <button
                            className="studio-outlink"
                            disabled={op.busy}
                            onClick={() =>
                              void op.run(async () => {
                                await post(workspace, `/editions/${e.id}/project`, {
                                  role: 'reading',
                                })
                                await reload()
                              })
                            }
                          >
                            加入项目
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
                      添加另一版本
                    </button>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <StudioEmpty title={query ? '没有匹配的资料' : '从一份想读懂的资料开始'}>
              收藏 PDF
              或题录，留下疑问，再把原文里的线索整理成自己的理解。已有阅读资料仍可从右侧打开。
            </StudioEmpty>
          )}
          {cursor && (
            <Action
              secondary
              disabled={op.busy}
              onClick={() => op.run(() => reload(undefined, cursor))}
            >
              加载更多资料
            </Action>
          )}
        </main>
        <aside className="studio-aside">
          <section className="studio-panel">
            <Library size={19} />
            <div className="studio-stat">{works.length}</div>
            <h2>属于你的资料库</h2>
            <p>
              {originals}{' '}
              个版本已保存原文。题录和原件在个人资料库中共享，项目里的筛选与证据独立保存。
            </p>
          </section>
          <section className="studio-panel">
            <h2>把已有积累带进来</h2>
            <p>支持 BibTeX、RIS、CSL-JSON。保留已有引用键，精确标识去重，相似标题留给你判断。</p>
            <div className="studio-actions" style={{ marginTop: 17 }}>
              <Action secondary onClick={() => open('import')}>
                导入书目
              </Action>
              <BibliographyExport workspace={workspace} works={works} />
            </div>
            <p className="studio-note">
              <Link className="studio-outlink" href={scopedUrl('/learning/reading', workspace)}>
                打开已有阅读资料
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
            ? '从一个问题，发现下一篇论文'
            : dialog === 'import'
              ? '导入已有书目'
              : dialog === 'attach'
                ? '保存这个版本的原文'
                : dialog === 'edition'
                  ? '添加可独立引用的版本'
                  : '收藏一份资料'
        }
        onClose={() => setDialog(null)}
        size={dialog === 'discover' ? 'xl' : 'lg'}
        busy={op.busy}
        className="studio-dialog"
        footer={
          dialog === 'discover' ? undefined : (
            <div className="studio-actions">
              <Action secondary onClick={() => setDialog(null)} disabled={op.busy}>
                取消
              </Action>
              <Action
                disabled={op.busy || (dialog === 'attach' && !file)}
                onClick={() =>
                  void op.run(async () => {
                    if (dialog === 'import') {
                      const result = await post<{ entries: { status: string; detail?: string }[] }>(
                        workspace,
                        '/bibliography/import',
                        { text: bibliography, format }
                      )
                      await reload()
                      const errors = result.entries.filter(e => e.status === 'error')
                      setNotice(
                        `已处理 ${result.entries.length} 条；${errors.length} 条需要检查。${errors.map(e => e.detail).join('；')}`
                      )
                      if (!errors.length) setDialog(null)
                    } else if (dialog === 'attach' && selected && file) {
                      await upload(selected, file)
                      await reload()
                      setDialog(null)
                    } else await add()
                  })
                }
              >
                {op.busy ? '正在保存…' : '保存到资料库'}
              </Action>
            </div>
          )
        }
      >
        <ErrorNotice message={op.error} />
        {notice && (
          <p className="studio-note" role="status">
            {notice}
          </p>
        )}
        {dialog === 'add' || dialog === 'edition' ? (
          <div className="studio-form">
            {dialog === 'add' && (
              <>
                <Field label="标题">
                  <input
                    value={title}
                    onChange={e => setTitle(e.target.value)}
                    placeholder="也可以先选择 PDF"
                  />
                </Field>
                <div className="studio-form-grid">
                  <Field label="作者（用分号分隔）">
                    <input value={authors} onChange={e => setAuthors(e.target.value)} />
                  </Field>
                  <Field label="年份">
                    <input type="number" value={year} onChange={e => setYear(e.target.value)} />
                  </Field>
                </div>
              </>
            )}
            <Field label="版本名称" hint="例如 arXiv v1、会议正式版；版本不会互相覆盖。">
              <input value={label} onChange={e => setLabel(e.target.value)} />
            </Field>
            <div className="studio-form-grid">
              <Field label="DOI（可选）">
                <input value={doi} onChange={e => setDoi(e.target.value)} />
              </Field>
              <Field label="arXiv ID（可选）">
                <input
                  value={arxiv}
                  onChange={e => setArxiv(e.target.value)}
                  placeholder="包含 v1 / v2 可精确区分版本"
                />
              </Field>
            </div>
            <label className="studio-upload">
              <Upload size={23} />
              <span>保存 PDF 原文，或稍后再补</span>
              <input
                type="file"
                accept="application/pdf"
                onChange={e => setFile(e.target.files?.[0] || null)}
              />
            </label>
          </div>
        ) : null}
        {dialog === 'attach' && (
          <label className="studio-upload">
            <Upload size={23} />
            <span>原件按内容保存，同一文件更名不会重复入库。</span>
            <input
              type="file"
              accept="application/pdf"
              onChange={e => setFile(e.target.files?.[0] || null)}
            />
          </label>
        )}
        {dialog === 'import' && (
          <div className="studio-form">
            <Field label="书目格式">
              <select value={format} onChange={e => setFormat(e.target.value)}>
                <option value="bibtex">BibTeX / BibLaTeX</option>
                <option value="ris">RIS</option>
                <option value="csl">CSL-JSON</option>
              </select>
            </Field>
            <input
              aria-label="选择书目文件"
              type="file"
              accept=".bib,.ris,.json"
              onChange={e => {
                const f = e.target.files?.[0]
                if (f) void f.text().then(setBibliography)
              }}
            />
            <Field label="书目内容">
              <textarea
                rows={12}
                value={bibliography}
                onChange={e => setBibliography(e.target.value)}
              />
            </Field>
          </div>
        )}
        {dialog === 'discover' && <DiscoveryPanel workspace={workspace} onCollected={reload} />}
      </Dialog>
    </StudioShell>
  )
}
