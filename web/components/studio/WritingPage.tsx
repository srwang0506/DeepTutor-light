'use client'
import { useCallback, useEffect, useRef, useState } from 'react'
import dynamic from 'next/dynamic'
import { useRouter } from 'next/navigation'
import WritingTools from './WritingTools'
import WritingAssistant from './WritingAssistant'
import { ArrowLeft, Download, FileCode2, Play, Plus, Save } from 'lucide-react'
import { useActiveWorkspaceId } from '@/hooks/useActiveWorkspaceId'
import { Dialog } from '@/shared/ui/Dialog'
import { apiFetch } from '@/lib/api'
import {
  records,
  post,
  studio,
  studioUrl,
  type StudioRecord,
  type ManuscriptData,
  type BuildData,
  type ProposalData,
  type SubmissionData,
} from '@/lib/studio-api'
import type { TextSelection } from './TexEditor'
import { PdfDocumentView } from '@/components/reading/PdfDocumentView'
import { Action, ErrorNotice, Field, StudioEmpty, StudioShell, useOperation } from './StudioShell'
const TexEditor = dynamic(() => import('./TexEditor'), {
  ssr: false,
  loading: () => <p className="studio-note">正在打开编辑器…</p>,
})
interface SourceFile {
  path: string
  hash: string
  size: number
  editable: boolean
}
interface OpenFile {
  path: string
  hash: string
  text: string
}
interface Recovery {
  journal: string
  path: string | null
  before?: string
  after?: string
  disk?: string
  before_text?: string
  after_text?: string
  disk_text?: string
  invalid?: boolean
  error?: string
}
interface Audit {
  missing_citations: string[]
  duplicate_keys: string[]
  unresolved_labels: string[]
  warnings: { path: string; line: number; kind: string; excerpt: string }[]
  stale_dependencies: unknown[]
}
const EMPTY_HASH = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
export default function WritingPage() {
  const workspace = useActiveWorkspaceId()
  return <WritingWorkspace key={workspace} workspace={workspace} />
}
function WritingWorkspace({ workspace }: { workspace: string }) {
  const router = useRouter()
  const draftKey = (document: string, path: string) =>
    `studio-draft:${workspace}:${document}:${path}`
  const op = useOperation(),
    [documents, setDocuments] = useState<StudioRecord<ManuscriptData>[]>([]),
    [active, setActive] = useState<StudioRecord<ManuscriptData> | null>(null),
    [files, setFiles] = useState<SourceFile[]>([]),
    [file, setFile] = useState<OpenFile | null>(null),
    [text, setText] = useState(''),
    [savedText, setSavedText] = useState(''),
    [saveStatus, setSaveStatus] = useState('saved'),
    [saveError, setSaveError] = useState(''),
    [builds, setBuilds] = useState<StudioRecord<BuildData>[]>([]),
    [proposals, setProposals] = useState<StudioRecord<ProposalData>[]>([]),
    [submissions, setSubmissions] = useState<StudioRecord<SubmissionData>[]>([]),
    [preview, setPreview] = useState(''),
    [jump, setJump] = useState<{ line: number; nonce: number } | undefined>(),
    [audit, setAudit] = useState<Audit | null>(null),
    [log, setLog] = useState(''),
    [recoveries, setRecoveries] = useState<Recovery[]>([]),
    [recovering, setRecovering] = useState<Recovery | null>(null),
    [selection, setSelection] = useState<TextSelection>({ start: 0, end: 0, text: '' })
  const [create, setCreate] = useState(false),
    [title, setTitle] = useState(''),
    [mainFile, setMainFile] = useState('main.tex'),
    [engine, setEngine] = useState('pdflatex'),
    [archive, setArchive] = useState<File | null>(null),
    [proposing, setProposing] = useState(false),
    [replacement, setReplacement] = useState(''),
    [rationale, setRationale] = useState(''),
    [accepting, setAccepting] = useState<StudioRecord<ProposalData> | null>(null),
    [factConfirmed, setFactConfirmed] = useState(false),
    [packing, setPacking] = useState(false),
    [allowlist, setAllowlist] = useState<string[]>([]),
    [newFile, setNewFile] = useState(false),
    [newPath, setNewPath] = useState(''),
    [conflict, setConflict] = useState<OpenFile | null>(null)
  const current = useRef({ active, file, text, savedText })
  current.current = { active, file, text, savedText }
  const saving = useRef<Promise<void> | null>(null),
    mounted = useRef(true),
    previewUrl = useRef('')
  const latest = builds.find(b => b.data.manuscript_id === active?.id),
    successful = builds.find(
      b => b.data.manuscript_id === active?.id && b.data.status === 'succeeded'
    )
  const previewStale =
    !!successful &&
    !!file &&
    (text !== savedText || successful.data.input_files[file.path] !== file.hash)
  const building = !!latest && ['queued', 'running'].includes(latest.data.status)
  const localProposals = proposals.filter(
    p => p.data.manuscript_id === active?.id && p.data.status === 'pending'
  )
  async function reload(signal?: AbortSignal) {
    const [a, b, c, d] = await Promise.all([
      records<ManuscriptData>(workspace, 'manuscript', signal),
      records<BuildData>(workspace, 'build', signal),
      records<ProposalData>(workspace, 'proposal', signal),
      records<SubmissionData>(workspace, 'submission', signal),
    ])
    setDocuments(a)
    setBuilds(b)
    setProposals(c)
    setSubmissions(d)
  }
  useEffect(() => {
    mounted.current = true
    const abort = new AbortController()
    reload(abort.signal).catch(e => {
      if (!abort.signal.aborted) op.setError(e.message)
    })
    return () => {
      mounted.current = false
      abort.abort()
      URL.revokeObjectURL(previewUrl.current)
    }
  }, [workspace]) // eslint-disable-line react-hooks/exhaustive-deps
  const save = useCallback(async () => {
    if (saving.current) {
      await saving.current
      return
    }
    const snapshot = current.current
    if (!snapshot.active || !snapshot.file || snapshot.text === snapshot.savedText) return
    setSaveStatus('saving')
    setSaveError('')
    const task = (async () => {
      try {
        const result = await studio<{ hash: string }>(
          workspace,
          `/manuscripts/${snapshot.active!.id}/file`,
          {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              path: snapshot.file!.path,
              base_hash: snapshot.file!.hash,
              text: snapshot.text,
            }),
          }
        )
        if (!mounted.current) return
        if (
          current.current.file?.path === snapshot.file!.path &&
          current.current.active?.id === snapshot.active!.id
        ) {
          setFile(f => (f ? { ...f, hash: result.hash, text: snapshot.text } : f))
          setSavedText(snapshot.text)
          current.current = {
            ...current.current,
            file: { ...snapshot.file!, hash: result.hash, text: snapshot.text },
            savedText: snapshot.text,
          }
          if (current.current.text === snapshot.text)
            sessionStorage.removeItem(draftKey(snapshot.active!.id, snapshot.file!.path))
          setSaveStatus('saved')
        }
      } catch (e) {
        if (mounted.current) {
          setSaveStatus('error')
          setSaveError(e instanceof Error ? e.message : '保存失败，本地编辑仍保留。')
        }
        throw e
      } finally {
        saving.current = null
      }
    })()
    saving.current = task
    await task
  }, [workspace])
  useEffect(() => {
    if (!active || !file || text === savedText || saveStatus === 'error') return
    const timer = setTimeout(() => {
      void save().catch(() => {})
    }, 1000)
    return () => clearTimeout(timer)
  }, [active, file, text, savedText, saveStatus, save])
  useEffect(() => {
    const before = (e: BeforeUnloadEvent) => {
      const c = current.current
      if (c.text !== c.savedText) {
        e.preventDefault()
        e.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', before)
    return () => window.removeEventListener('beforeunload', before)
  }, [])
  async function flush() {
    await save()
    if (current.current.text !== current.current.savedText) await save()
    if (current.current.text !== current.current.savedText)
      throw new Error('请等待当前修改保存后再继续。')
  }
  useEffect(() => {
    const switching = (event: Event) => {
      ;(event as CustomEvent<Promise<void>[]>).detail.push(flush())
    }
    const navigating = (event: MouseEvent) => {
      const state = current.current
      if (
        state.text === state.savedText ||
        event.defaultPrevented ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey ||
        event.button !== 0
      )
        return
      const link = (event.target as Element)?.closest('a')
      if (!link || link.target === '_blank' || link.hasAttribute('download')) return
      const href = link.getAttribute('href')
      if (!href || !href.startsWith('/')) return
      event.preventDefault()
      event.stopPropagation()
      void flush()
        .then(() => router.push(href))
        .catch(() => {})
    }
    window.addEventListener('deeptutor:before-workspace-switch', switching)
    document.addEventListener('click', navigating, true)
    return () => {
      window.removeEventListener('deeptutor:before-workspace-switch', switching)
      document.removeEventListener('click', navigating, true)
    }
  })
  function changeText(value: string) {
    setText(value)
    current.current = { ...current.current, text: value }
    const c = current.current
    if (c.active && c.file) {
      try {
        sessionStorage.setItem(
          draftKey(c.active.id, c.file.path),
          JSON.stringify({ hash: c.file.hash, text: value })
        )
      } catch {
        setSaveError('浏览器无法暂存草稿，请保存后再离开页面。')
      }
    }
  }
  async function openFile(identifier: string, path: string) {
    await flush()
    const source = await studio<OpenFile>(
      workspace,
      `/manuscripts/${identifier}/file?path=${encodeURIComponent(path)}`
    )
    let recovered: { hash: string; text: string } | null = null
    try {
      const raw = sessionStorage.getItem(draftKey(identifier, path))
      if (raw) {
        const data = JSON.parse(raw)
        if (typeof data.text === 'string' && typeof data.hash === 'string') recovered = data
      }
    } catch {}
    const restored = recovered?.text ?? source.text
    setFile(source)
    setText(restored)
    setSavedText(source.text)
    const conflicting = !!recovered && recovered.hash !== source.hash && restored !== source.text
    setSaveStatus(conflicting ? 'error' : 'saved')
    setSaveError(conflicting ? '已恢复未保存的编辑，但磁盘版本也发生变化。请比较后合并。' : '')
    if (conflicting) setConflict(source)
    setSelection({ start: 0, end: 0, text: '' })
    current.current = { ...current.current, file: source, text: restored, savedText: source.text }
  }
  async function openDocument(row: StudioRecord<ManuscriptData>) {
    await flush()
    const listing = await studio<{ files: SourceFile[] }>(workspace, `/manuscripts/${row.id}/files`)
    setActive(row)
    current.current = { ...current.current, active: row, file: null, text: '', savedText: '' }
    setFile(null)
    setText('')
    setSavedText('')
    setFiles(listing.files)
    const recovery = await studio<{ conflicts: Recovery[] }>(
      workspace,
      `/manuscripts/${row.id}/recovery`
    )
    setRecoveries(recovery.conflicts)
    setAudit(null)
    setLog('')
    if (listing.files.some(f => f.path === row.data.main_file))
      await openFile(row.id, row.data.main_file)
  }
  async function newDocument() {
    let row: StudioRecord<ManuscriptData>
    if (archive) {
      const form = new FormData()
      form.set('file', archive)
      form.set('title', title)
      form.set('main_file', mainFile)
      form.set('engine', engine)
      row = await studio(workspace, '/manuscripts/import', { method: 'POST', body: form })
    } else row = await post(workspace, '/manuscripts', { title, main_file: mainFile, engine })
    setCreate(false)
    await reload()
    await openDocument(row)
  }
  async function compile() {
    if (!active) return
    await flush()
    const row = await post<StudioRecord<BuildData>>(
      workspace,
      `/manuscripts/${active.id}/compile`,
      {}
    )
    await reload()
    setBuilds(current => [row, ...current.filter(b => b.id !== row.id)])
    setLog('')
  }
  useEffect(() => {
    if (!building) return
    const abort = new AbortController()
    let pending = false
    const timer = window.setInterval(() => {
      if (pending || document.visibilityState === 'hidden') return
      pending = true
      records<BuildData>(workspace, 'build', abort.signal)
        .then(setBuilds)
        .catch(e => {
          if (!abort.signal.aborted) op.setError(e.message)
        })
        .finally(() => {
          pending = false
        })
    }, 1500)
    return () => {
      window.clearInterval(timer)
      abort.abort()
    }
  }, [building, workspace]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!latest || ['queued', 'running', 'succeeded'].includes(latest.data.status)) {
      setLog('')
      return
    }
    if (!latest.data.outputs?.['build.log']) {
      setLog('构建已中断或取消。可以重试当时的输入快照。')
      return
    }
    const abort = new AbortController()
    apiFetch(studioUrl(`/builds/${latest.id}/build.log`, workspace), { signal: abort.signal })
      .then(async r => {
        if (!r.ok) throw new Error('构建日志暂时无法读取')
        return r.text()
      })
      .then(setLog)
      .catch(e => {
        if (!abort.signal.aborted) op.setError(e.message)
      })
    return () => abort.abort()
  }, [latest?.id, latest?.data.status, workspace]) // eslint-disable-line react-hooks/exhaustive-deps
  async function resolveRecovery(choice: 'disk' | 'before' | 'prepared') {
    if (!active || !recovering) return
    await post(workspace, `/manuscripts/${active.id}/recovery`, {
      journal: recovering.journal,
      disk_hash: recovering.disk || '',
      choice,
    })
    setRecovering(null)
    setSaveError('')
    // A resolution chooses a reviewed disk version; discard only this file's
    // browser draft after preserving every recovery version on the server.
    if (recovering.path) sessionStorage.removeItem(draftKey(active.id, recovering.path))
    current.current = { ...current.current, file: null, text: '', savedText: '' }
    await openDocument(active)
  }
  useEffect(() => {
    let live = true
    const abort = new AbortController()
    if (!successful) {
      URL.revokeObjectURL(previewUrl.current)
      previewUrl.current = ''
      setPreview('')
      return
    }
    apiFetch(studioUrl(`/builds/${successful.id}/preview.pdf`, workspace), { signal: abort.signal })
      .then(async response => {
        if (!response.ok) throw new Error('无法载入构建预览。')
        return response.blob()
      })
      .then(blob => {
        if (live) {
          URL.revokeObjectURL(previewUrl.current)
          previewUrl.current = URL.createObjectURL(blob)
          setPreview(previewUrl.current)
        }
      })
      .catch(e => {
        if (!abort.signal.aborted) op.setError(e.message)
      })
    return () => {
      live = false
      abort.abort()
    }
  }, [successful?.id, workspace]) // eslint-disable-line react-hooks/exhaustive-deps
  async function inspect() {
    if (!active) return
    await flush()
    setAudit(await studio(workspace, `/manuscripts/${active.id}/audit`))
  }
  async function propose() {
    if (!active || !file) return
    await flush()
    await post(workspace, `/manuscripts/${active.id}/proposals`, {
      path: file.path,
      base_hash: current.current.file!.hash,
      text: replacement,
      start: selection.start,
      end: selection.end,
      rationale,
      source_ids: [],
    })
    setProposing(false)
    await reload()
  }
  async function accept() {
    if (!accepting || !active) return
    await flush()
    await post(workspace, `/proposals/${accepting.id}/accept`, {
      expected_revision: accepting.revision,
      confirm_facts: factConfirmed,
    })
    const path = accepting.data.path
    setAccepting(null)
    await openFile(active.id, path)
    await reload()
  }
  async function pack() {
    if (!active || !successful) return
    await post(workspace, `/manuscripts/${active.id}/submissions`, {
      paths: allowlist,
      build_id: successful.id,
    })
    setPacking(false)
    await reload()
  }
  async function addFile() {
    if (!active) return
    await flush()
    await studio(workspace, `/manuscripts/${active.id}/file`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: newPath, base_hash: EMPTY_HASH, text: '' }),
    })
    setFiles(
      (await studio<{ files: SourceFile[] }>(workspace, `/manuscripts/${active.id}/files`)).files
    )
    setNewFile(false)
    await openFile(active.id, newPath)
  }
  async function inspectConflict() {
    if (!active || !file) return
    setConflict(
      await studio(
        workspace,
        `/manuscripts/${active.id}/file?path=${encodeURIComponent(file.path)}`
      )
    )
  }
  function useRemote() {
    if (!conflict) return
    setFile(conflict)
    setText(conflict.text)
    setSavedText(conflict.text)
    setSaveStatus('saved')
    setSaveError('')
    current.current = {
      ...current.current,
      file: conflict,
      text: conflict.text,
      savedText: conflict.text,
    }
    if (active && file) sessionStorage.removeItem(draftKey(active.id, file.path))
    setConflict(null)
  }
  function mergeLocal() {
    if (!conflict) return
    setFile(conflict)
    setSavedText(conflict.text)
    current.current = { ...current.current, file: conflict, savedText: conflict.text }
    setSaveError('')
    setSaveStatus('saved')
    setConflict(null)
  }
  async function download(path: string, name: string) {
    const response = await apiFetch(studioUrl(path, workspace))
    if (!response.ok) throw new Error('下载失败，请重试。')
    const url = URL.createObjectURL(await response.blob())
    const a = document.createElement('a')
    a.href = url
    a.download = name
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  return (
    <StudioShell
      workspace={workspace}
      active="writing"
      title={active ? active.data.title : '让论点与证据，一起成稿。'}
      description={
        active
          ? '真实的多文件 LaTeX 工程。编辑自动保存，构建与投稿各自保留版本。'
          : '从现有模板开始，把原文证据、实验结果和自己的判断，写成可以交付的论文。'
      }
      action={
        active ? (
          <Action
            secondary
            onClick={() =>
              op.run(async () => {
                await flush()
                setActive(null)
                setFile(null)
              })
            }
          >
            <ArrowLeft size={14} />
            所有论文
          </Action>
        ) : (
          <Action
            onClick={() => {
              setTitle('')
              setArchive(null)
              setCreate(true)
            }}
          >
            <Plus size={14} />
            新建论文工程
          </Action>
        )
      }
    >
      <ErrorNotice message={op.error} />
      <ErrorNotice message={saveError} />
      {saveError && (
        <Action secondary onClick={() => op.run(inspectConflict)}>
          比较磁盘版本与当前编辑
        </Action>
      )}
      {!active ? (
        <div className="studio-card-list">
          {documents.map(row => (
            <article className="studio-card" key={row.id}>
              <div className="studio-actions">
                <FileCode2 size={20} />
                <h3 style={{ margin: 0 }}>{row.data.title}</h3>
              </div>
              <p className="studio-note">
                {row.data.main_file} · {row.data.engine} · 修订 {row.revision}
              </p>
              <Action secondary onClick={() => op.run(() => openDocument(row))} disabled={op.busy}>
                打开工程
              </Action>
            </article>
          ))}
          {!documents.length && (
            <StudioEmpty title="保留你熟悉的论文工程">
              导入包含 TeX、Bib、样式和图片的
              ZIP，或新建一个基础工程。源文件保留在本地，可以用外部编辑器继续修改。
            </StudioEmpty>
          )}
        </div>
      ) : (
        <>
          <div className="studio-toolbar">
            <div className="studio-actions">
              <Action
                onClick={() => op.run(compile)}
                disabled={op.busy || !!saveError || building || !!recoveries.length}
              >
                <Play size={13} />
                {building
                  ? latest?.data.status === 'queued'
                    ? '等待编译…'
                    : '正在编译…'
                  : op.busy
                    ? '处理中…'
                    : '编译预览'}
              </Action>
              {building && (
                <Action
                  secondary
                  disabled={op.busy}
                  onClick={() =>
                    op.run(async () => {
                      await post(workspace, `/builds/${latest!.id}/cancel`, {})
                      await reload()
                    })
                  }
                >
                  取消构建
                </Action>
              )}
              {latest && ['failed', 'interrupted', 'cancelled'].includes(latest.data.status) && (
                <Action
                  secondary
                  disabled={op.busy}
                  onClick={() =>
                    op.run(async () => {
                      await post(workspace, `/builds/${latest.id}/retry`, {})
                      await reload()
                    })
                  }
                >
                  重试原快照
                </Action>
              )}
              <Action secondary onClick={() => op.run(inspect)} disabled={op.busy}>
                检查引用与依赖
              </Action>
              <Action
                secondary
                disabled={!selection.text || op.busy}
                onClick={() => {
                  setReplacement(selection.text)
                  setRationale('')
                  setProposing(true)
                }}
              >
                审阅选区修改
              </Action>
              <WritingAssistant
                key={`assist:${active.id}`}
                workspace={workspace}
                manuscript={active.id}
                file={file ? { ...file, text } : null}
                selection={selection}
                flush={flush}
                proposed={async row => {
                  await reload()
                  setFactConfirmed(false)
                  setAccepting(row)
                }}
              />
            </div>
            <Action
              secondary
              disabled={!successful || op.busy}
              onClick={() => {
                setAllowlist(Object.keys(successful!.data.input_files))
                setPacking(true)
              }}
            >
              <Download size={13} />
              投稿快照
            </Action>
          </div>
          <WritingTools
            key={active.id}
            workspace={workspace}
            manuscript={active.id}
            file={file ? { ...file, text } : null}
            selection={selection}
            flush={flush}
            insert={snippet => {
              const current = Array.from(text)
              if (current.slice(selection.start, selection.end).join('') !== selection.text) {
                op.setError('选区已变化，请重新选择插入位置。')
                return
              }
              changeText(
                current.slice(0, selection.start).join('') +
                  snippet +
                  current.slice(selection.end).join('')
              )
            }}
            openFile={async (path, line) => {
              await openFile(active.id, path)
              if (line) setJump({ line, nonce: Date.now() })
            }}
            refreshFiles={async () => {
              setFiles(
                (
                  await studio<{ files: SourceFile[] }>(
                    workspace,
                    `/manuscripts/${active.id}/files`
                  )
                ).files
              )
            }}
          />
          {!!recoveries.length && (
            <section className="studio-error" role="alert">
              <strong>发现中断保存后的版本冲突</strong>
              <p>磁盘文件、保存前版本和待保存版本都已保留。比较后选择需要继续使用的版本。</p>
              {recoveries.map(item => (
                <Action key={item.journal} secondary onClick={() => setRecovering(item)}>
                  {item.path || '检查损坏的保存日志'}
                </Action>
              ))}
            </section>
          )}
          <div className="studio-split">
            <section className="studio-editor">
              <aside className="studio-file-tree">
                {files.map(item => (
                  <button
                    key={item.path}
                    aria-current={file?.path === item.path}
                    disabled={!item.editable || op.busy}
                    onClick={() => op.run(() => openFile(active.id, item.path))}
                  >
                    {item.path}
                  </button>
                ))}
                <button
                  onClick={() => {
                    setNewPath('')
                    setNewFile(true)
                  }}
                >
                  <Plus size={12} style={{ display: 'inline' }} /> 新建文件
                </button>
              </aside>
              <div className="studio-editor-main">
                <div className="studio-editor-toolbar">
                  <span>{file?.path || '选择文件'}</span>
                  <span className="studio-muted" role="status">
                    {saveStatus === 'saving'
                      ? '保存中…'
                      : saveStatus === 'error'
                        ? '修改保留在编辑器'
                        : text !== savedText
                          ? '等待保存'
                          : '已保存'}
                  </span>
                  <button className="studio-link-button" onClick={() => op.run(save)}>
                    <Save size={12} />
                  </button>
                </div>
                {file && (
                  <TexEditor
                    identity={`${active.id}:${file.path}`}
                    value={text}
                    onChange={changeText}
                    onSelection={setSelection}
                    jump={jump}
                  />
                )}
              </div>
            </section>
            <section className="studio-preview">
              <header className="studio-editor-toolbar">
                <span>PDF 预览</span>
                <span className="studio-muted">
                  {previewStale
                    ? '源文件已更新，预览来自上次构建'
                    : building
                      ? '后台构建中，显示上一成功版本'
                      : latest && latest.data.status !== 'succeeded'
                        ? '最近构建未成功，保留上次预览'
                        : successful
                          ? '最近成功构建'
                          : '尚未编译'}
                </span>
              </header>
              {preview ? (
                <div className="studio-build-pdf">
                  <PdfDocumentView
                    materialId={`build:${successful?.id}`}
                    sourceUrl={preview}
                    unitCount={1}
                    annotations={[]}
                    jump={null}
                    onSelection={() => {}}
                  />
                </div>
              ) : (
                <StudioEmpty title="保存，然后编译">
                  预览来自固定的源文件快照；编译失败时，上一份成功的 PDF 会继续保留。
                </StudioEmpty>
              )}
            </section>
          </div>
          {log && (
            <section style={{ marginTop: 20 }}>
              <h3 className="studio-section-title">编译日志</h3>
              <pre className="studio-diff">{log}</pre>
            </section>
          )}
          {audit && (
            <section className="studio-panel" style={{ marginTop: 20 }}>
              <h2>静态审校</h2>
              <p>检查引用键、交叉引用、匿名信息和来源版本。仍需核对论点是否准确表达原始证据。</p>
              <div className="studio-form-grid" style={{ marginTop: 18 }}>
                {[
                  ['未解析引用', audit.missing_citations],
                  ['重复引用键', audit.duplicate_keys],
                  ['未解析交叉引用', audit.unresolved_labels],
                ].map(([label, rows]) => (
                  <div key={String(label)}>
                    <h3 style={{ fontSize: 12 }}>{String(label)}</h3>
                    <p>{((rows as string[]) || []).join(', ') || '未发现'}</p>
                  </div>
                ))}
              </div>
              {audit.warnings?.map((warning, i) => (
                <p className="studio-note" key={i}>
                  {warning.path}:{warning.line} · 需要核对匿名要求：{warning.excerpt}
                </p>
              ))}
              {!!audit.stale_dependencies?.length && (
                <p className="studio-error">
                  {audit.stale_dependencies.length} 项依赖已变化，需要重新核对相关论点或表图。
                </p>
              )}
            </section>
          )}
          {localProposals.length > 0 && (
            <section>
              <h3 className="studio-section-title">待审阅的修改</h3>
              <div className="studio-card-list">
                {localProposals.map(row => (
                  <article className="studio-card" key={row.id}>
                    <h3>{row.data.path}</h3>
                    <pre className="studio-diff">{row.data.diff}</pre>
                    <footer>
                      <span className="studio-note">
                        {row.data.fact_changes.length
                          ? `${row.data.fact_changes.length} 类事实变化需要确认`
                          : '未检测到规则覆盖的事实变化，仍需审阅'}
                      </span>
                      <Action
                        secondary
                        onClick={() => {
                          setAccepting(row)
                          setFactConfirmed(false)
                        }}
                      >
                        审阅并接受
                      </Action>
                    </footer>
                  </article>
                ))}
              </div>
            </section>
          )}
          {submissions.some(s => s.data.manuscript_id === active.id) && (
            <section>
              <h3 className="studio-section-title">已冻结的投稿版本</h3>
              <div className="studio-card-list">
                {submissions
                  .filter(s => s.data.manuscript_id === active.id)
                  .map(row => (
                    <article className="studio-card" key={row.id}>
                      <h3>{new Date(row.created * 1000).toLocaleString('zh-CN')}</h3>
                      <p>
                        {Object.keys(row.data.files).length} 个源文件 · 固定构建{' '}
                        {row.data.build_id.slice(0, 8)}
                      </p>
                      <footer>
                        <span className="studio-note">后续编辑不会改变这份快照。</span>
                        <Action
                          secondary
                          onClick={() =>
                            op.run(() =>
                              download(`/submissions/${row.id}/download`, 'submission.zip')
                            )
                          }
                        >
                          <Download size={13} />
                          下载 ZIP
                        </Action>
                      </footer>
                    </article>
                  ))}
              </div>
            </section>
          )}
        </>
      )}
      <Dialog
        open={create}
        onClose={() => !op.busy && setCreate(false)}
        title="新建论文工程"
        description="可以导入自己的模板 ZIP，也可以先建立一个可编辑的基础工程。"
        size="md"
        className="studio-dialog"
        footer={
          <Action onClick={() => op.run(newDocument)} disabled={op.busy || !title.trim()}>
            创建工程
          </Action>
        }
      >
        <div className="studio-form">
          <ErrorNotice message={op.error} />
          <Field label="论文或项目名称">
            <input value={title} onChange={e => setTitle(e.target.value)} />
          </Field>
          <Field label="现有工程 ZIP · 可选">
            <input
              type="file"
              accept=".zip"
              onChange={e => setArchive(e.target.files?.[0] || null)}
            />
          </Field>
          <Field label="主 TeX 文件">
            <input value={mainFile} onChange={e => setMainFile(e.target.value)} />
          </Field>
          <Field label="编译器">
            <select value={engine} onChange={e => setEngine(e.target.value)}>
              <option value="pdflatex">pdfLaTeX</option>
              <option value="xelatex">XeLaTeX</option>
              <option value="lualatex">LuaLaTeX</option>
            </select>
          </Field>
        </div>
      </Dialog>
      <Dialog
        open={proposing}
        onClose={() => !op.busy && setProposing(false)}
        title="审阅选区修改"
        description="修改先生成对照提案，确认后才应用到源文件。"
        size="lg"
        className="studio-dialog"
        footer={
          <Action onClick={() => op.run(propose)} disabled={op.busy}>
            生成修改对照
          </Action>
        }
      >
        <div className="studio-form">
          <ErrorNotice message={op.error} />
          <Field label="原文">
            <pre className="studio-diff">{selection.text}</pre>
          </Field>
          <Field label="建议文本">
            <textarea
              rows={8}
              value={replacement}
              onChange={e => setReplacement(e.target.value)}
              spellCheck={false}
            />
          </Field>
          <Field label="修改理由">
            <textarea value={rationale} onChange={e => setRationale(e.target.value)} />
          </Field>
        </div>
      </Dialog>
      <Dialog
        open={!!accepting}
        onClose={() => !op.busy && setAccepting(null)}
        title="确认这次修改"
        size="lg"
        className="studio-dialog"
        footer={
          <Action
            onClick={() => op.run(accept)}
            disabled={op.busy || (!!accepting?.data.fact_changes.length && !factConfirmed)}
          >
            接受并写入源文件
          </Action>
        }
      >
        <ErrorNotice message={op.error} />
        <pre className="studio-diff">{accepting?.data.diff}</pre>
        {accepting?.data.fact_changes.map(change => (
          <div className="studio-card" key={change.kind} style={{ marginTop: 12 }}>
            <h3>{change.kind}</h3>
            <p>
              {change.before.join(' · ') || '无'} → {change.after.join(' · ') || '无'}
            </p>
          </div>
        ))}
        {!!accepting?.data.fact_changes.length && (
          <label className="studio-check">
            <input
              type="checkbox"
              checked={factConfirmed}
              onChange={e => setFactConfirmed(e.target.checked)}
            />
            我已核对数值、单位、引用和论断范围的变化。
          </label>
        )}
      </Dialog>
      <Dialog
        open={packing}
        onClose={() => !op.busy && setPacking(false)}
        title="生成投稿快照"
        description="从最近成功构建中选择要导出的文件。PDF 会一并加入，工程目录之外的资料不会包含在内。"
        size="md"
        className="studio-dialog"
        footer={
          <Action onClick={() => op.run(pack)} disabled={op.busy || !allowlist.length}>
            冻结此版本
          </Action>
        }
      >
        <ErrorNotice message={op.error} />
        {successful &&
          Object.keys(successful.data.input_files).map(path => (
            <label className="studio-check" key={path}>
              <input
                type="checkbox"
                checked={allowlist.includes(path)}
                onChange={e =>
                  setAllowlist(a => (e.target.checked ? [...a, path] : a.filter(p => p !== path)))
                }
              />
              {path}
            </label>
          ))}
        <p className="studio-note">
          这里导出的是成功构建时的源文件。未重新编译的修改不会混入这份投稿快照。
        </p>
      </Dialog>
      <Dialog
        open={newFile}
        onClose={() => !op.busy && setNewFile(false)}
        title="添加源文件"
        size="sm"
        className="studio-dialog"
        footer={
          <Action onClick={() => op.run(addFile)} disabled={op.busy || !newPath.trim()}>
            创建文件
          </Action>
        }
      >
        <ErrorNotice message={op.error} />
        <Field label="相对路径">
          <input
            value={newPath}
            onChange={e => setNewPath(e.target.value)}
            placeholder="sections/method.tex"
          />
        </Field>
      </Dialog>
      <Dialog open={!!recovering} title="恢复中断的保存" onClose={() => setRecovering(null)}>
        {recovering?.invalid ? (
          <p className="studio-note">保存日志无法解析。保留磁盘文件，归档原日志供检查。</p>
        ) : (
          <div className="studio-form-grid">
            {(
              [
                ['保存前', recovering?.before_text],
                ['待保存', recovering?.after_text],
                ['磁盘当前', recovering?.disk_text],
              ] as const
            ).map(([label, value]) => (
              <div key={label}>
                <h3>{label}</h3>
                <pre className="studio-diff">{value}</pre>
              </div>
            ))}
          </div>
        )}
        <div className="studio-actions">
          <Action disabled={op.busy} onClick={() => op.run(() => resolveRecovery('disk'))}>
            保留磁盘版本
          </Action>
          {!recovering?.invalid && (
            <>
              <Action
                secondary
                disabled={op.busy}
                onClick={() => op.run(() => resolveRecovery('prepared'))}
              >
                恢复待保存版本
              </Action>
              <Action
                secondary
                disabled={op.busy}
                onClick={() => op.run(() => resolveRecovery('before'))}
              >
                恢复保存前版本
              </Action>
            </>
          )}
        </div>
      </Dialog>
      <Dialog
        open={!!conflict}
        onClose={() => setConflict(null)}
        title="外部修改需要合并"
        description="当前编辑仍保留。比较两个版本，再决定如何继续。"
        size="lg"
        className="studio-dialog"
        footer={
          <>
            <Action secondary onClick={useRemote}>
              使用磁盘版本
            </Action>
            <Action onClick={mergeLocal}>以当前编辑覆盖，重新保存</Action>
          </>
        }
      >
        <div className="studio-split">
          <div>
            <h3>磁盘版本</h3>
            <pre className="studio-diff">{conflict?.text}</pre>
          </div>
          <div>
            <h3>当前编辑</h3>
            <pre className="studio-diff">{text}</pre>
          </div>
        </div>
      </Dialog>
    </StudioShell>
  )
}
