'use client'
import { useTranslation } from 'react-i18next'
import { studioLabel } from './display'
import { getLocale } from '@/lib/datetime'
import { useState } from 'react'
import Link from 'next/link'
import { BookMarked, Link2, ListTree, MessageSquareText, Plus } from 'lucide-react'
import { Dialog } from '@/shared/ui/Dialog'
import { scopedUrl } from '@/lib/workspace-scope'
import { post, records, studio, type StudioRecord, type Work } from '@/lib/studio-api'
import { Action, ErrorNotice, Field, useOperation } from './StudioShell'
import type { TextSelection } from './TexEditor'

type Citation = { citekey: string; title: string; authors: string[]; path: string }
type Source = StudioRecord<{
  title?: string
  label?: string
  quote?: string
  edition_id?: string
  blob_hash?: string
  page?: number
  origin?: string
  status?: string
  verification?: string
}>
type Passage = StudioRecord<{
  path: string
  quote: string
  note: string
  source_ids: string[]
  sources: Source[]
}> & { location: { status: string; line?: number }; stale: unknown[] }
type Review = StudioRecord<{
  submission_id: string
  comment: string
  response: string
  status: string
  source_ids: string[]
  changes: { path: string; hash: string; line?: string }[]
}>
export default function WritingTools({
  workspace,
  manuscript,
  file,
  selection,
  flush,
  insert,
  openFile,
  refreshFiles,
}: {
  workspace: string
  manuscript: string
  file: { path: string; hash: string; text: string } | null
  selection: TextSelection
  flush: () => Promise<void>
  insert: (text: string) => void
  openFile: (path: string, line?: number) => Promise<void>
  refreshFiles: () => Promise<void>
}) {
  const { t, i18n } = useTranslation()

  const [panel, setPanel] = useState<'citations' | 'evidence' | 'reviews' | 'outline' | null>(null)
  const [query, setQuery] = useState(''),
    [keys, setKeys] = useState<string[]>([]),
    [sources, setSources] = useState<Source[]>([])
  const [citations, setCitations] = useState<Citation[]>([]),
    [works, setWorks] = useState<Work[]>([]),
    [passages, setPassages] = useState<Passage[]>([])
  const [sourceIds, setSourceIds] = useState<string[]>([]),
    [note, setNote] = useState(''),
    [notice, setNotice] = useState<{
      key: string
      values?: Record<string, string | number>
    } | null>(null)
  const [edition, setEdition] = useState(''),
    [citekey, setCitekey] = useState('')
  const [reviews, setReviews] = useState<Review[]>([]),
    [submissions, setSubmissions] = useState<
      StudioRecord<{ manuscript_id: string; created: number }>[]
    >([])
  const [review, setReview] = useState<Review | null>(null),
    [comment, setComment] = useState(''),
    [response, setResponse] = useState(''),
    [submission, setSubmission] = useState(''),
    [status, setStatus] = useState('open'),
    [attachCurrent, setAttachCurrent] = useState(false)
  const [outline, setOutline] = useState<
    { path: string; title: string; kind: string; line: number }[]
  >([])
  const op = useOperation()
  const toggle = (id: string) =>
    setSourceIds((ids) => (ids.includes(id) ? ids.filter((s) => s !== id) : [...ids, id]))
  async function exportResponses() {
    if (!submission) return
    const result = await studio<{ content: string; filename: string; unresolved: string[] }>(
      workspace,
      `/submissions/${submission}/response-export`,
    )
    const url = URL.createObjectURL(
      new Blob([result.content], { type: 'text/markdown;charset=utf-8' }),
    )
    const link = document.createElement('a')
    link.href = url
    link.download = result.filename
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    setNotice(
      result.unresolved.length
        ? {
            key: '已导出回复草稿，仍有 {{value1}} 条意见或证据需要复核。',
            values: { value1: result.unresolved.length },
          }
        : { key: '已导出回复草稿，请检查后提交。' },
    )
  }
  async function loadSources() {
    const rows = await Promise.all(
      ['evidence', 'observation', 'artifact', 'claim', 'run'].map((kind) =>
        records<Source['data']>(workspace, kind),
      ),
    )
    setSources(rows.flat())
  }
  async function show(next: typeof panel) {
    await flush()
    setQuery('')
    setNotice(null)
    setSourceIds([])
    setNote('')
    setKeys([])
    setPanel(next)
    if (next === 'citations') {
      const [a, b] = await Promise.all([
        studio<{ entries: Citation[]; duplicate_keys: string[] }>(
          workspace,
          `/manuscripts/${manuscript}/citations`,
        ),
        studio<{ works: Work[] }>(workspace, '/library'),
      ])
      setCitations(a.entries)
      setWorks(b.works)
      if (a.duplicate_keys.length)
        setNotice({
          key: '引用键重复，需要先整理：{{value1}}',
          values: { value1: a.duplicate_keys.join(', ') },
        })
    } else if (next === 'evidence') {
      await loadSources()
      setPassages(
        (await studio<{ passages: Passage[] }>(workspace, `/manuscripts/${manuscript}/passages`))
          .passages,
      )
    } else if (next === 'reviews') {
      await loadSources()
      const all = (
        await records<{ manuscript_id: string; created: number }>(workspace, 'submission')
      ).filter((s) => s.data.manuscript_id === manuscript)
      setSubmissions(all)
      setSubmission(all[0]?.id || '')
      setReviews(
        (await records<Review['data']>(workspace, 'review')).filter((r) =>
          all.some((s) => s.id === r.data.submission_id),
        ),
      )
      setReview(null)
      setComment('')
      setResponse('')
      setStatus('open')
      setAttachCurrent(false)
    } else if (next === 'outline')
      setOutline(
        (await studio<{ outline: typeof outline }>(workspace, `/manuscripts/${manuscript}/outline`))
          .outline,
      )
  }
  async function addCitation() {
    await post(workspace, `/manuscripts/${manuscript}/citations`, { edition_id: edition, citekey })
    await refreshFiles()
    const result = await studio<{ entries: Citation[] }>(
      workspace,
      `/manuscripts/${manuscript}/citations`,
    )
    setCitations(result.entries)
    setKeys((k) => [...k, citekey])
    setEdition('')
    setCitekey('')
    setNotice({
      key: '已加入 studio-references.bib。请在模板现有的 bibliography / addbibresource 命令中加入此文件；原宏和旧引用键保持不变。',
    })
  }
  async function bind() {
    if (!file) return
    await post(workspace, `/manuscripts/${manuscript}/passages`, {
      path: file.path,
      base_hash: file.hash,
      start: selection.start,
      end: selection.end,
      source_ids: sourceIds,
      note,
    })
    setPassages(
      (await studio<{ passages: Passage[] }>(workspace, `/manuscripts/${manuscript}/passages`))
        .passages,
    )
    setNotice({ key: '已保存这段原文与来源版本。关联本身不表示来源已支持全部结论。' })
    setSourceIds([])
  }
  async function saveReview() {
    const body = {
      submission_id: submission,
      comment,
      response,
      status,
      source_ids: sourceIds,
      changes:
        attachCurrent && file
          ? [
              {
                path: file.path,
                hash: file.hash,
                line: String(
                  file.text
                    .slice(0, Array.from(file.text).slice(0, selection.start).join('').length)
                    .split('\n').length,
                ),
              },
            ]
          : review?.data.changes || [],
    }
    if (review)
      await studio(workspace, `/reviews/${review.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ ...body, expected_revision: review.revision }),
      })
    else await post(workspace, '/reviews', body)
    setReviews(
      (await records<Review['data']>(workspace, 'review')).filter((r) =>
        submissions.some((s) => s.id === r.data.submission_id),
      ),
    )
    setReview(null)
    setComment('')
    setResponse('')
    setSourceIds([])
    setAttachCurrent(false)
    setStatus('open')
    setNotice({ key: '审稿记录已保存，原投稿快照仍然保留。' })
  }
  function pickReview(row: Review) {
    setReview(row)
    setSubmission(row.data.submission_id)
    setComment(row.data.comment)
    setResponse(row.data.response)
    setStatus(row.data.status)
    setSourceIds(row.data.source_ids)
    setAttachCurrent(false)
  }
  const filteredSources = sources.filter((row) =>
    `${row.data.title || row.data.label || ''} ${row.kind}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  )
  const sourcePicker = (
    <div className="studio-source-picker">
      {filteredSources.map((row) => (
        <label className="studio-source-choice" key={row.id}>
          <input
            type="checkbox"
            checked={sourceIds.includes(row.id)}
            onChange={() => toggle(row.id)}
          />
          <span>
            <strong>{row.data.title || row.data.label || row.id}</strong>
            <small>
              {studioLabel(t, row.kind)} ·{' '}
              {studioLabel(t, row.data.origin || row.data.verification || row.data.status) || t('来源记录')} {t('· v')}
              {row.revision}
            </small>
          </span>
        </label>
      ))}
      {!sources.length && (
        <p className="studio-note">{t('先在阅读、证据或实验工作台保存一条来源。')}</p>
      )}
    </div>
  )
  return (
    <>
      <div className="studio-writing-tools">
        <Action
          secondary
          disabled={op.busy || !file}
          onClick={() => op.run(() => show('citations'))}
        >
          <BookMarked size={14} />
          {t('插入引用')}
        </Action>
        <Action secondary disabled={op.busy} onClick={() => op.run(() => show('evidence'))}>
          <Link2 size={14} />
          {t('稿段与证据')}
        </Action>
        <Action secondary disabled={op.busy} onClick={() => op.run(() => show('outline'))}>
          <ListTree size={14} />
          {t('章节与标签')}
        </Action>
        <Action secondary disabled={op.busy} onClick={() => op.run(() => show('reviews'))}>
          <MessageSquareText size={14} />
          {t('审稿与回复')}
        </Action>
      </div>
      <ErrorNotice message={op.error} />
      <Dialog
        open={!!panel}
        size="lg"
        className="studio-dialog"
        title={
          panel === 'citations'
            ? t('稿件书目')
            : panel === 'evidence'
              ? t('把一句话连回证据')
              : panel === 'reviews'
                ? t('审稿意见与逐条回复')
                : t('章节与标签')
        }
        onClose={() => setPanel(null)}
      >
        <ErrorNotice message={op.error} />
        {notice && (
          <p className="studio-note" role="status">
            {t(notice.key, notice.values || {})}
          </p>
        )}
        {panel === 'citations' && (
          <>
            <Field label={t("搜索标题、作者或引用键")}>
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t('在这份稿件和资料库中查找…')}
              />
            </Field>
            <h3 className="studio-section-title">{t('当前稿件 · 保留已有引用键')}</h3>
            <div className="studio-source-picker">
              {citations
                .filter((c) =>
                  `${c.citekey} ${c.title} ${c.authors.join(' ')}`
                    .toLowerCase()
                    .includes(query.toLowerCase()),
                )
                .map((c, i) => (
                  <label className="studio-source-choice" key={`${c.path}:${c.citekey}:${i}`}>
                    <input
                      type="checkbox"
                      checked={keys.includes(c.citekey)}
                      onChange={() =>
                        setKeys((k) =>
                          k.includes(c.citekey)
                            ? k.filter((v) => v !== c.citekey)
                            : [...k, c.citekey],
                        )
                      }
                    />
                    <span>
                      <strong>{c.title || c.citekey}</strong>
                      <small>
                        {c.citekey} · {c.path}
                      </small>
                    </span>
                  </label>
                ))}
              {!citations.length && (
                <p className="studio-note">{t('工程还没有书目条目。可以从资料库选择一篇加入。')}</p>
              )}
            </div>
            <Action
              disabled={!keys.length || op.busy || !file?.path.endsWith('.tex')}
              onClick={() => {
                insert(`\\cite{${keys.join(',')}}`)
                setPanel(null)
              }}
            >
              {keys.length ? t("插入 {{value1}} 个引用", { value1: keys.length }) : t('选择引用后插入')}
            </Action>
            <h3 className="studio-section-title">{t('从资料库加入')}</h3>
            <Field label={t("文献版本")}>
              <select
                value={edition}
                onChange={(e) => {
                  setEdition(e.target.value)
                  const found = works
                    .flatMap((w) => w.editions)
                    .find((v) => v.id === e.target.value)
                  setCitekey(found?.data.citekey || `ref${e.target.value.slice(0, 10)}`)
                }}
              >
                <option value="">{t('选择文献')}</option>
                {works
                  .filter((w) =>
                    `${w.data.title} ${w.data.authors.join(' ')}`
                      .toLowerCase()
                      .includes(query.toLowerCase()),
                  )
                  .flatMap((w) =>
                    w.editions.map((e) => (
                      <option key={e.id} value={e.id}>
                        {w.data.title} · {e.data.label}
                      </option>
                    )),
                  )}
              </select>
            </Field>
            {edition && (
              <div className="studio-form-grid">
                <Field label={t("在这份稿件中的引用键")}>
                  <input value={citekey} onChange={(e) => setCitekey(e.target.value)} />
                </Field>
                <Action disabled={!citekey || op.busy} onClick={() => op.run(addCitation)}>
                  <Plus size={13} />
                  {t('加入书目')}
                </Action>
              </div>
            )}
          </>
        )}
        {panel === 'evidence' && (
          <>
            {selection.text && file ? (
              <>
                <blockquote className="studio-passage-quote">{selection.text}</blockquote>
                <Field label={t("检索项目来源")}>
                  <input value={query} onChange={(e) => setQuery(e.target.value)} />
                </Field>
                {sourcePicker}
                <Field label={t("这些来源支持或限制了什么")}>
                  <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} />
                </Field>
                <Action disabled={!sourceIds.length || op.busy} onClick={() => op.run(bind)}>
                  {t('关联当前选区')}
                </Action>
              </>
            ) : (
              <p className="studio-note">
                {t('先在编辑器中选中一句话或一段论证，再关联原文、实验或表图。')}
              </p>
            )}
            <h3 className="studio-section-title">{t('已关联的稿段')}</h3>
            {passages.map((p) => (
              <article className="studio-card" key={p.id}>
                <blockquote className="studio-passage-quote">{p.data.quote}</blockquote>
                <p className="studio-note">
                  {p.data.path}:{p.location.line || '?'} ·{' '}
                  {p.location.status === 'current'
                    ? t('原位置')
                    : p.location.status === 'exact_relocated'
                      ? t('已按唯一原文重新定位')
                      : t('位置需重新核对')}
                  {p.stale.length ? t(" · {{value1}} 项来源已更新", { value1: p.stale.length }) : ''}
                </p>
                <p>{p.data.note}</p>
                <div className="studio-source-links">
                  {p.data.sources.map((source) =>
                    source.kind === 'evidence' ? (
                      <Link
                        key={source.id}
                        href={scopedUrl(
                          `/library/${source.data.edition_id}?file=${source.data.blob_hash}&page=${source.data.page}`,
                          workspace,
                        )}
                      >
                        {source.data.label || t('查看原文')} ↗
                      </Link>
                    ) : (
                      <Link
                        key={source.id}
                        href={scopedUrl(
                          `/objects/${source.id}?revision=${source.revision}`,
                          workspace,
                        )}
                      >
                        {source.data.title || studioLabel(t, source.kind)} {t('· v')}
                        {source.revision} ↗
                      </Link>
                    ),
                  )}
                </div>
              </article>
            ))}
          </>
        )}
        {panel === 'outline' && (
          <div className="studio-source-picker">
            {outline.map((row, i) => (
              <button
                className="studio-outline-row"
                key={i}
                onClick={() =>
                  op.run(async () => {
                    await openFile(row.path, row.line)
                    setPanel(null)
                  })
                }
              >
                <span>{row.title}</span>
                <small>
                  {studioLabel(t, row.kind)} · {row.path}:{row.line}
                </small>
              </button>
            ))}
            {!outline.length && (
              <p className="studio-note">
                {t('还没有章节或标签。复杂宏中的标题可能需要直接查找源文件。')}
              </p>
            )}
          </div>
        )}
        {panel === 'reviews' && (
          <>
            {!submissions.length ? (
              <p className="studio-note">{t('先保存一次投稿快照，再记录针对该版本的审稿意见。')}</p>
            ) : (
              <>
                {!!reviews.length && (
                  <div className="studio-source-picker">
                    {reviews.map((r) => (
                      <button
                        className="studio-outline-row"
                        key={r.id}
                        onClick={() => pickReview(r)}
                      >
                        <span>{r.data.comment}</span>
                        <small>
                          {r.data.status === 'addressed'
                            ? t('已回应')
                            : r.data.status === 'working'
                              ? t('修订中')
                              : t('待处理')}
                        </small>
                      </button>
                    ))}
                  </div>
                )}
                <Action
                  secondary
                  disabled={op.busy || !submission}
                  onClick={() => op.run(exportResponses)}
                >
                  {t('导出本版审稿回复')}
                </Action>
                <h3 className="studio-section-title">{review ? t('编辑这条意见') : t('记录新意见')}</h3>
                <Field label={t("原投稿版本")}>
                  <select
                    disabled={!!review}
                    value={submission}
                    onChange={(e) => setSubmission(e.target.value)}
                  >
                    {submissions.map((s) => (
                      <option value={s.id} key={s.id}>
                        {new Date(s.created * 1000).toLocaleString(getLocale(i18n.resolvedLanguage || i18n.language))} · {s.id.slice(0, 8)}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label={t("审稿意见")}>
                  <textarea rows={3} value={comment} onChange={(e) => setComment(e.target.value)} />
                </Field>
                <Field label={t("逐条回应草稿")}>
                  <textarea
                    rows={5}
                    value={response}
                    onChange={(e) => setResponse(e.target.value)}
                  />
                </Field>
                <Field label={t("处理状态")}>
                  <select value={status} onChange={(e) => setStatus(e.target.value)}>
                    <option value="open">{t('待处理')}</option>
                    <option value="working">{t('修订中')}</option>
                    <option value="addressed">{t('已回应')}</option>
                  </select>
                </Field>
                <h3 className="studio-section-title">{t('回应依据')}</h3>
                {sourcePicker}
                {file && (
                  <label className="studio-source-choice">
                    <input
                      type="checkbox"
                      checked={attachCurrent}
                      onChange={(e) => setAttachCurrent(e.target.checked)}
                    />
                    <span>
                      {t('记录当前修改位置 ·')}
                      {file.path}
                      <small>{t('保存源文件哈希与行号，之后的修改不会冒充这次记录。')}</small>
                    </span>
                  </label>
                )}
                <Action
                  disabled={
                    !comment.trim() ||
                    !submission ||
                    op.busy ||
                    (status === 'addressed' && !response.trim())
                  }
                  onClick={() => op.run(saveReview)}
                >
                  {t('保存意见与回复')}
                </Action>
              </>
            )}
          </>
        )}
      </Dialog>
    </>
  )
}
