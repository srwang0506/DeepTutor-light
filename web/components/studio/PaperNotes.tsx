'use client'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Dialog } from '@/shared/ui/Dialog'
import {
  archiveComment,
  parseArchivedComments,
  parseReviews,
  reviewFormat,
  reviewMarkers,
  reviewReply,
} from '@/lib/latexcoder/review'
import { reopenCommentChanges } from '@/lib/latexcoder/comment-history'
import { Action, Field } from './StudioShell'
import type { TextSelection } from './TexEditor'

export default function PaperNotes({
  path,
  text,
  selection,
  save,
  close,
}: {
  path: string
  text: string
  selection: TextSelection
  save: (text: string) => void
  close: () => void
}) {
  const { t } = useTranslation()
  const [note, setNote] = useState(''),
    [reply, setReply] = useState(''),
    [replyTo, setReplyTo] = useState('')
  const [invalid, setInvalid] = useState(false)
  const [kind, setKind] = useState<'comment' | 'revision' | 'deletion'>('comment')
  const items = parseReviews(text),
    archived = parseArchivedComments(text)
  const format = reviewFormat(path)
  const author = 'Author'
  const replaceRange = (from: number, to: number, value: string) =>
    save(text.slice(0, from) + value + text.slice(to))
  const start = Array.from(text).slice(0, selection.start).join('').length
  const end = start + selection.text.length
  function create() {
    const id = crypto.randomUUID(),
      value = note.trim()
    const message = kind === 'revision' ? selection.text : kind === 'deletion' ? '' : value
    const body = kind === 'revision' ? value : selection.text
    const pair = reviewMarkers(kind, id, author, format, message)
    const marked = pair.opening + body + pair.closing
    const parsed = parseReviews(marked)
    if (
      parsed.length !== 1 ||
      parsed[0].body !== body ||
      parsed[0].note !== message ||
      parsed[0].to !== marked.length
    ) {
      setInvalid(true)
      return
    }
    replaceRange(start, end, marked)
    setNote('')
    setInvalid(false)
  }
  return (
    <Dialog open title={t('文内批注')} onClose={close} size="md" className="studio-dialog">
      <div className="writer-dialog">
        {selection.text &&
        text.slice(start, end) === selection.text &&
        ![...items, ...archived].some(i => i.from < end && start < i.to) ? (
          <>
            <blockquote>{selection.text.slice(0, 320)}</blockquote>
            <div className="writer-switch">
              <button aria-pressed={kind === 'comment'} onClick={() => setKind('comment')}>
                {t('批注')}
              </button>
              <button aria-pressed={kind === 'revision'} onClick={() => setKind('revision')}>
                {t('建议改写')}
              </button>
              <button aria-pressed={kind === 'deletion'} onClick={() => setKind('deletion')}>
                {t('建议删除')}
              </button>
            </div>
            {kind !== 'deletion' && (
              <Field label={kind === 'revision' ? t('建议文本') : t('给这段原文留个批注')}>
                <textarea
                  value={note}
                  maxLength={1500}
                  rows={2}
                  onChange={e => setNote(e.target.value)}
                />
              </Field>
            )}
            <Action disabled={kind !== 'deletion' && !note.trim()} onClick={create}>
              {kind === 'comment' ? t('添加批注') : t('保留为修改建议')}
            </Action>
          </>
        ) : (
          <p className="studio-note">{t('在源文件里选中一段文字即可批注。')}</p>
        )}
        {invalid && <p role="alert">{t('批注中有不成对的大括号或保留标记，请调整后重试。')}</p>}
        <ul className="writer-rows">
          {items.map((item, index) => (
            <li key={item.id + index}>
              <blockquote>{item.body.slice(0, 320)}</blockquote>
              <p>{item.note}</p>
              {item.replies.map(r => (
                <p key={r.id}>{r.body}</p>
              ))}
              {item.kind === 'comment' ? (
                <div className="studio-actions">
                  <Action
                    secondary
                    onClick={() => {
                      setReplyTo(item.id)
                      setReply('')
                    }}
                  >
                    {t('回复')}
                  </Action>
                  <Action
                    secondary
                    onClick={() =>
                      replaceRange(
                        item.from,
                        item.to,
                        item.body + archiveComment(item, 'resolved', author, format)
                      )
                    }
                  >
                    {t('标为已解决')}
                  </Action>
                </div>
              ) : (
                <div className="studio-actions">
                  <Action
                    secondary
                    onClick={() =>
                      replaceRange(item.from, item.to, item.kind === 'deletion' ? '' : item.body)
                    }
                  >
                    {t('接受修改')}
                  </Action>
                  <Action
                    secondary
                    onClick={() =>
                      replaceRange(
                        item.from,
                        item.to,
                        item.kind === 'revision'
                          ? item.note
                          : item.kind === 'deletion'
                            ? item.body
                            : ''
                      )
                    }
                  >
                    {t('撤销这项修改')}
                  </Action>
                </div>
              )}
              {replyTo === item.id && (
                <>
                  <Field label={t('回复内容')}>
                    <textarea
                      value={reply}
                      maxLength={1500}
                      rows={2}
                      onChange={e => setReply(e.target.value)}
                    />
                  </Field>
                  <Action
                    disabled={!reply.trim() || item.replyInsertAt == null}
                    onClick={() => {
                      const at = item.replyInsertAt
                      if (at == null) return
                      const id = crypto.randomUUID(),
                        marker = reviewReply({ id, author, body: reply.trim() }, format)
                      const updated = text.slice(0, at) + marker + text.slice(at)
                      if (
                        !parseReviews(updated)
                          .find(r => r.id === item.id)
                          ?.replies.some(r => r.id === id && r.body === reply.trim())
                      ) {
                        setInvalid(true)
                        return
                      }
                      save(updated)
                      setReplyTo('')
                      setInvalid(false)
                    }}
                  >
                    {t('保存回复')}
                  </Action>
                </>
              )}
            </li>
          ))}
        </ul>
        {!!archived.length && (
          <details>
            <summary>
              {t('已归档的批注')} · {archived.length}
            </summary>
            {archived.map((item, index) => {
              const edits = reopenCommentChanges(text, item, format)
              return (
                <div key={item.id + index}>
                  <p>{item.note}</p>
                  <Action
                    secondary
                    disabled={!edits}
                    onClick={() => {
                      let next = text
                      for (const e of [...(edits || [])].reverse())
                        next = next.slice(0, e.from) + e.insert + next.slice(e.to)
                      save(next)
                    }}
                  >
                    {t('重新打开批注')}
                  </Action>
                </div>
              )
            })}
          </details>
        )}
      </div>
    </Dialog>
  )
}
