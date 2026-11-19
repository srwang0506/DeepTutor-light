'use client'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Dialog } from '@/shared/ui/Dialog'
import { FileInput } from '@/shared/ui/FileInput'
import { apiFetch } from '@/lib/api'
import { post, studio, studioUrl } from '@/lib/studio-api'
import { Action, ErrorNotice, Field, useOperation } from './StudioShell'
import type { PaperFile } from './paper-navigation'

export type ProjectPanel = 'search' | 'history' | 'files'
type Version = { commit: string; title: string; time: number }
type Difference = {
  commit: string
  restore_id: string
  files: { path: string; status: string; diff: string }[]
}
type Match = { path: string; line: number; before: string; after: string | null; excerpt: string }
type Matches = { matches: Match[]; total: number; files: number; preview_id: string }

export default function PaperProjectTools({
  panel,
  workspace,
  manuscript,
  file,
  main,
  onClose,
  flush,
  changed,
  navigate,
}: {
  panel: ProjectPanel
  workspace: string
  manuscript: string
  file?: PaperFile
  main: string
  onClose: () => void
  flush: () => Promise<void>
  changed: (path?: string) => Promise<void>
  navigate: (path: string, line?: number) => Promise<void>
}) {
  const { t, i18n } = useTranslation()
  const op = useOperation()
  const base = '/manuscripts/' + manuscript
  const [query, setQuery] = useState(''),
    [replacement, setReplacement] = useState('')
  const [replace, setReplace] = useState(false),
    [regexp, setRegexp] = useState(false),
    [exact, setExact] = useState(false)
  const [found, setFound] = useState<Matches | null>(null)
  const [versions, setVersions] = useState<Version[]>([]),
    [title, setTitle] = useState('')
  const [difference, setDifference] = useState<Difference | null>(null),
    [message, setMessage] = useState('')
  const [blame, setBlame] = useState<{ line: number; title: string; commit: string }[] | null>(null)
  const [bundle, setBundle] = useState<File | null>(null)
  const [incoming, setIncoming] = useState<{
    commit: string
    files: string[]
    conflicts: string[]
    preview_id: string
  } | null>(null)
  const [upload, setUpload] = useState<File | null>(null),
    [path, setPath] = useState('')
  const [removing, setRemoving] = useState(false),
    [destination, setDestination] = useState('')
  const [trash, setTrash] = useState<{ id: string; data: { path: string } }[]>([])
  const searchArgs = {
    query,
    replacement: replace ? replacement : null,
    regexp,
    case_sensitive: exact,
  }
  const { setError } = op
  function versionTitle(value: string) {
    const match = /^(Before restore|Git import) · ([0-9a-f]{10})$/.exec(value)
    if (match) return t(match[1]) + ' · ' + match[2]
    return ['Before Git import', 'Source export'].includes(value) ? t(value) : value
  }
  useEffect(() => {
    const abort = new AbortController()
    const route = panel === 'history' ? '/versions' : panel === 'files' ? '/trash' : null
    if (route)
      void studio<Version[] | typeof trash>(workspace, base + route, {}, abort.signal)
        .then(rows => {
          if (panel === 'history') setVersions(rows as Version[])
          else setTrash(rows as typeof trash)
        })
        .catch(e => {
          if (!abort.signal.aborted) setError(e.message)
        })
    return () => abort.abort()
  }, [panel, workspace, base, setError])
  async function download(bundle: boolean) {
    await flush()
    const res = await apiFetch(
      studioUrl(base + (bundle ? '/history.bundle' : '/source.zip'), workspace),
      { method: bundle ? 'POST' : 'GET' }
    )
    if (!res.ok) throw new Error(t('下载失败，请重试。'))
    const url = URL.createObjectURL(await res.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = bundle ? 'paper.bundle' : 'paper.zip'
    link.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 5000)
    if (bundle) setVersions(await studio(workspace, base + '/versions'))
  }
  async function fileChange(to?: string) {
    if (!file) return
    await flush()
    await post(workspace, base + '/files/change', {
      path: file.path,
      base_hash: file.hash,
      destination: to || null,
    })
    setRemoving(false)
    await changed(to || main)
    setTrash(await studio(workspace, base + '/trash'))
    setMessage(to ? t('文件已移动。') : t('文件已放入废纸篓。'))
  }
  return (
    <Dialog
      open
      onClose={() => {
        if (!op.busy) onClose()
      }}
      className="studio-dialog"
      size="lg"
      title={
        panel === 'search' ? t('搜索整篇论文') : panel === 'history' ? t('版本历史') : t('工程文件')
      }
    >
      <div className="writer-dialog" aria-busy={op.busy}>
        <fieldset disabled={op.busy}>
          {panel === 'search' && (
            <>
              <form
                onSubmit={e => {
                  e.preventDefault()
                  void op.run(async () => {
                    if (!query) return
                    await flush()
                    setMessage('')
                    setFound(await post(workspace, base + '/search', searchArgs))
                  })
                }}
              >
                <Field label={t('查找内容')}>
                  <input
                    autoFocus
                    value={query}
                    maxLength={1000}
                    onChange={e => {
                      setQuery(e.target.value)
                      setFound(null)
                    }}
                  />
                </Field>
                <div className="writer-options">
                  <label>
                    <input
                      type="checkbox"
                      checked={exact}
                      onChange={e => {
                        setExact(e.target.checked)
                        setFound(null)
                      }}
                    />
                    {t('区分大小写')}
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={regexp}
                      onChange={e => {
                        setRegexp(e.target.checked)
                        setFound(null)
                      }}
                    />
                    {t('正则表达式')}
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={replace}
                      onChange={e => {
                        setReplace(e.target.checked)
                        setFound(null)
                      }}
                    />
                    {t('替换文本')}
                  </label>
                </div>
                {replace && (
                  <Field label={t('替换为')}>
                    <input
                      value={replacement}
                      maxLength={10000}
                      onChange={e => {
                        setReplacement(e.target.value)
                        setFound(null)
                      }}
                    />
                  </Field>
                )}
                <button className="studio-button" type="submit" disabled={!query}>
                  {replace ? t('预览替换') : t('查找')}
                </button>
              </form>
              {found && (
                <>
                  <p role="status">{t('共 {{count}} 处匹配', { count: found.total })}</p>
                  <ul className="writer-rows">
                    {found.matches.map((m, i) => (
                      <li key={i}>
                        <button
                          className="writer-row"
                          onClick={() =>
                            op.run(async () => {
                              await navigate(m.path, m.line)
                              onClose()
                            })
                          }
                        >
                          <small>
                            {m.path} · {m.line}
                          </small>
                          <span>{m.excerpt}</span>
                          {replace && (
                            <span>
                              <del>{m.before}</del> → <ins>{m.after}</ins>
                            </span>
                          )}
                        </button>
                      </li>
                    ))}
                  </ul>
                  {found.total > found.matches.length && (
                    <p>{t('仅显示前 {{count}} 处匹配。', { count: found.matches.length })}</p>
                  )}
                  {replace && found.files > 0 && (
                    <Action
                      onClick={() =>
                        op.run(async () => {
                          await flush()
                          await post(workspace, base + '/search', {
                            ...searchArgs,
                            preview_id: found.preview_id,
                          })
                          await changed()
                          setFound(null)
                          setMessage(t('替换已保存。'))
                        })
                      }
                    >
                      {t('应用到 {{count}} 个文件', { count: found.files })}
                    </Action>
                  )}
                </>
              )}
            </>
          )}
          {panel === 'history' && (
            <>
              <Field label={t('版本名称')}>
                <input
                  placeholder={t('例如：投稿前初稿')}
                  value={title}
                  maxLength={250}
                  onChange={e => setTitle(e.target.value)}
                />
              </Field>
              <div className="studio-actions">
                <Action
                  disabled={!title.trim()}
                  onClick={() =>
                    op.run(async () => {
                      await flush()
                      const v = await post<{ created: boolean }>(workspace, base + '/versions', {
                        title,
                      })
                      setTitle('')
                      setDifference(null)
                      setVersions(await studio(workspace, base + '/versions'))
                      setMessage(v.created ? t('版本已保存。') : t('当前稿件已与最近版本一致。'))
                    })
                  }
                >
                  {t('保存版本')}
                </Action>
                <Action secondary onClick={() => op.run(() => download(true))}>
                  {t('下载 Git 历史')}
                </Action>
                {file?.editable && (
                  <Action
                    secondary
                    onClick={() =>
                      op.run(async () => {
                        const response = await studio<{ lines: NonNullable<typeof blame> }>(
                          workspace,
                          base + '/attribution?path=' + encodeURIComponent(file.path)
                        )
                        setBlame(response.lines)
                      })
                    }
                  >
                    {t('查看逐行来源')}
                  </Action>
                )}
              </div>
              <details>
                <summary>{t('与本地 Git 或 Codex 交换稿件')}</summary>
                <p className="studio-note">
                  {t(
                    '下载 Git 历史后，在本机克隆并编辑。提交修改，再生成回传文件。导入前可以核对受影响的文件。'
                  )}
                </p>
                <pre className="studio-diff">
                  {
                    'git clone paper.bundle paper\ncd paper\n\ngit add -A\ngit commit -m "Revise manuscript"\ngit bundle create ../changes.bundle HEAD'
                  }
                </pre>
                <Field label={t('选择修改后的 Git bundle')}>
                  <FileInput
                    accept=".bundle"
                    onChange={e => {
                      setBundle(e.target.files?.[0] || null)
                      setIncoming(null)
                    }}
                  />
                </Field>
                <Action
                  secondary
                  disabled={!bundle}
                  onClick={() =>
                    op.run(async () => {
                      if (!bundle) return
                      await flush()
                      const form = new FormData()
                      form.set('file', bundle)
                      setIncoming(
                        await studio(workspace, base + '/versions/import', {
                          method: 'POST',
                          body: form,
                        })
                      )
                    })
                  }
                >
                  {t('比较本地修改')}
                </Action>
                {incoming && (
                  <>
                    <p>
                      {t('变更文件')} · {incoming.commit.slice(0, 8)}
                    </p>
                    <ul>
                      {incoming.files.map(name => (
                        <li key={name}>
                          {name} {incoming.conflicts.includes(name) && t('两边都已修改')}
                        </li>
                      ))}
                    </ul>
                    <Action
                      disabled={!bundle || !!incoming.conflicts.length}
                      onClick={() =>
                        op.run(async () => {
                          if (!bundle) return
                          await flush()
                          const form = new FormData()
                          form.set('file', bundle)
                          form.set('preview_id', incoming.preview_id)
                          await studio(workspace, base + '/versions/import', {
                            method: 'POST',
                            body: form,
                          })
                          await changed()
                          setIncoming(null)
                          setBundle(null)
                          setVersions(await studio(workspace, base + '/versions'))
                          setMessage(t('本地修改已合入稿件。'))
                        })
                      }
                    >
                      {t('合入当前稿件')}
                    </Action>
                  </>
                )}
              </details>
              {blame && (
                <details open>
                  <summary>{t('最近版本中各行的来源')}</summary>
                  {blame.length ? (
                    <pre className="studio-diff">
                      {blame
                        .map(
                          r => r.line + '   ' + r.commit.slice(0, 8) + '   ' + versionTitle(r.title)
                        )
                        .join('\n')}
                    </pre>
                  ) : (
                    <p>{t('此文件还没有保存过版本。')}</p>
                  )}
                </details>
              )}
              {!versions.length && (
                <p className="studio-note">{t('保存第一个版本，就能比较改动或恢复旧稿。')}</p>
              )}
              <ul className="writer-rows">
                {versions.map(v => (
                  <li key={v.commit}>
                    <button
                      className="writer-row"
                      onClick={() =>
                        op.run(async () => {
                          await flush()
                          setMessage('')
                          setDifference(await studio(workspace, base + '/versions/' + v.commit))
                        })
                      }
                    >
                      <span>{versionTitle(v.title)}</span>
                      <small>
                        {new Date(v.time * 1000).toLocaleString(i18n.language)} ·{' '}
                        {v.commit.slice(0, 8)}
                      </small>
                    </button>
                  </li>
                ))}
              </ul>
              {difference && (
                <div>
                  <strong>{t('恢复此版本会产生的改动')}</strong>
                  {difference.files.map(f => (
                    <details key={f.path}>
                      <summary>
                        {f.path} ·{' '}
                        {f.status === 'add'
                          ? t('添加')
                          : f.status === 'remove'
                            ? t('移除')
                            : t('修改')}
                      </summary>
                      <pre className="studio-diff">{f.diff || t('文件内容有变化。')}</pre>
                    </details>
                  ))}
                  {difference.files.length ? (
                    <Action
                      onClick={() =>
                        op.run(async () => {
                          await flush()
                          await post(
                            workspace,
                            base + '/versions/' + difference.commit + '/restore',
                            { restore_id: difference.restore_id }
                          )
                          await changed(main)
                          setDifference(null)
                          setVersions(await studio(workspace, base + '/versions'))
                          setMessage(t('版本已恢复，恢复前的稿件也已留存。'))
                        })
                      }
                    >
                      {t('确认恢复此版本')}
                    </Action>
                  ) : (
                    <p>{t('当前稿件与此版本一致。')}</p>
                  )}
                </div>
              )}
            </>
          )}
          {panel === 'files' && (
            <>
              <div className="studio-actions">
                <Action secondary onClick={() => op.run(() => download(false))}>
                  {t('下载当前源码 ZIP')}
                </Action>
              </div>
              <Field label={t('上传工程文件')}>
                <FileInput
                  onChange={e => {
                    const f = e.target.files?.[0] || null
                    setUpload(f)
                    setPath(f?.name || '')
                  }}
                />
              </Field>
              {upload && (
                <>
                  <Field label={t('工程内路径')}>
                    <input value={path} maxLength={500} onChange={e => setPath(e.target.value)} />
                  </Field>
                  <Action
                    disabled={!path.trim()}
                    onClick={() =>
                      op.run(async () => {
                        await flush()
                        const data = new FormData()
                        data.set('file', upload)
                        data.set('path', path)
                        await studio(workspace, base + '/files/upload', {
                          method: 'POST',
                          body: data,
                        })
                        await changed()
                        setUpload(null)
                        setPath('')
                        setMessage(t('文件已上传。'))
                      })
                    }
                  >
                    {t('上传')}
                  </Action>
                </>
              )}
              {file && file.path !== main && (
                <>
                  <Field label={t('移动或重命名当前文件')}>
                    <input
                      value={destination}
                      placeholder={file.path}
                      maxLength={500}
                      onChange={e => setDestination(e.target.value)}
                    />
                  </Field>
                  <div className="studio-actions">
                    <Action
                      secondary
                      disabled={!destination.trim()}
                      onClick={() => op.run(() => fileChange(destination.trim()))}
                    >
                      {t('移动文件')}
                    </Action>
                    <Action
                      secondary
                      onClick={() => (removing ? op.run(() => fileChange()) : setRemoving(true))}
                    >
                      {removing ? t('确认移入废纸篓') : t('移入废纸篓')}
                    </Action>
                  </div>
                </>
              )}
              {!!trash.length && (
                <details open>
                  <summary>{t('废纸篓')}</summary>
                  <ul className="writer-rows">
                    {trash.map(item => (
                      <li key={item.id}>
                        <span>{item.data.path}</span>
                        <Action
                          secondary
                          onClick={() =>
                            op.run(async () => {
                              await flush()
                              await post(workspace, base + '/trash/' + item.id + '/restore', {})
                              await changed()
                              setTrash(await studio(workspace, base + '/trash'))
                              setMessage(t('文件已恢复。'))
                            })
                          }
                        >
                          {t('恢复文件')}
                        </Action>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </>
          )}
        </fieldset>
        <ErrorNotice message={op.error} />
        <p className="studio-note" role="status">
          {message}
        </p>
      </div>
    </Dialog>
  )
}
