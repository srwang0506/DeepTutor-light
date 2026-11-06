'use client'

import { useEffect, useState } from 'react'
import { Check, ChevronLeft, ChevronRight, Pencil, Plus, RotateCcw, Trash2, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import './note-cards.css'

type Card = {
  front: string
  back: string
  hint?: string
  _study?: { id?: string }
  [key: string]: unknown
}

/** Card text belongs to the note. Scheduling belongs to the existing review store. */
export default function NoteFlashcards({
  data,
  readOnly,
  onChange,
}: {
  data: string
  readOnly: boolean
  onChange: (props: { data: string }) => void
}) {
  const { i18n } = useTranslation()
  const zh = i18n.language.startsWith('zh')
  let payload: Record<string, unknown> = {}
  try {
    payload = JSON.parse(data || '{}')
  } catch {
    /* An empty deck stays editable. */
  }
  const cards = (Array.isArray(payload.cards) ? payload.cards : []).filter(
    (card): card is Card => card && typeof card === 'object' && typeof card.front === 'string'
  )
  const [index, setIndex] = useState(0)
  const [flipped, setFlipped] = useState(false)
  const [draft, setDraft] = useState<Card | null>(null)
  const [adding, setAdding] = useState(false)
  const current = cards[Math.min(index, cards.length - 1)]
  // New cards receive identity before their first edit/save. Legacy cards arrive
  // with server-assigned legacy GUIDs, and are never replaced with random IDs.
  useEffect(() => {
    if (readOnly) return
    let value: Record<string, unknown>
    try {
      value = JSON.parse(data || '{}')
    } catch {
      return
    }
    if (!Array.isArray(value.cards)) return
    const seen = new Set<string>()
    let changed = false
    const normalized = value.cards.map((card: Card) => {
      if (!card || typeof card !== 'object' || typeof card.front !== 'string') return card
      const id = card._study?.id
      if (id && !seen.has(id)) {
        seen.add(id)
        return card
      }
      changed = true
      const next = `card_${crypto.randomUUID().replaceAll('-', '')}`
      seen.add(next)
      return { ...card, _study: { id: next } }
    })
    if (changed) onChange({ data: JSON.stringify({ ...value, cards: normalized }) })
  }, [data, readOnly, onChange])
  const save = () => {
    if (!draft?.front.trim() || !draft.back.trim()) return
    const next = adding ? [...cards, draft] : cards.map((card, i) => (i === index ? draft : card))
    onChange({ data: JSON.stringify({ ...payload, cards: next }) })
    if (adding) setIndex(next.length - 1)
    setDraft(null)
    setFlipped(false)
  }
  const create = () => {
    setAdding(true)
    setDraft({
      front: '',
      back: '',
      _study: { id: `card_${crypto.randomUUID().replaceAll('-', '')}` },
    })
  }
  return (
    <section className="dt-note-cards" aria-label={zh ? '笔记闪卡' : 'Notebook flashcards'}>
      <header>
        <span>{zh ? '独立回忆' : 'Active recall'}</span>
        <small>
          {cards.length
            ? `${Math.min(index + 1, cards.length)} / ${cards.length}`
            : zh
              ? '从一个问题开始'
              : 'Start with a question'}
        </small>
        {!readOnly && (
          <button
            onClick={create}
            title={zh ? '添加卡片' : 'Add card'}
            aria-label={zh ? '添加卡片' : 'Add card'}
          >
            <Plus size={15} />
          </button>
        )}
      </header>
      {draft ? (
        <div className="dt-note-card-editor">
          <label>
            {zh ? '问题' : 'Question'}
            <textarea
              autoFocus
              value={draft.front}
              onChange={e => setDraft({ ...draft, front: e.target.value })}
              rows={3}
            />
          </label>
          <label>
            {zh ? '答案' : 'Answer'}
            <textarea
              value={draft.back}
              onChange={e => setDraft({ ...draft, back: e.target.value })}
              rows={4}
            />
          </label>
          <p>
            {zh
              ? '内容修改保留卡片身份。实质变更会在下次复习时让你选择排期。'
              : 'Identity is preserved. Substantive changes ask you to choose the review schedule.'}
          </p>
          <footer>
            <button onClick={() => setDraft(null)}>
              <X size={14} />
              {zh ? '取消' : 'Cancel'}
            </button>
            <button
              className="is-primary"
              disabled={!draft.front.trim() || !draft.back.trim()}
              onClick={save}
            >
              <Check size={14} />
              {zh ? '保存卡片' : 'Save card'}
            </button>
          </footer>
        </div>
      ) : current ? (
        <>
          <button className="dt-note-card-face" onClick={() => setFlipped(!flipped)}>
            <small>
              {flipped
                ? zh
                  ? '答案'
                  : 'Answer'
                : zh
                  ? '先回忆，再翻面'
                  : 'Recall before revealing'}
            </small>
            <span>{flipped ? current.back : current.front}</span>
          </button>
          <footer>
            <button
              disabled={index === 0}
              onClick={() => {
                setIndex(i => i - 1)
                setFlipped(false)
              }}
              aria-label={zh ? '上一张' : 'Previous card'}
            >
              <ChevronLeft size={16} />
            </button>
            <button onClick={() => setFlipped(!flipped)}>
              <RotateCcw size={14} />
              {zh ? '翻面' : 'Flip'}
            </button>
            {!readOnly && (
              <button
                onClick={() => {
                  setAdding(false)
                  setDraft(structuredClone(current))
                }}
              >
                <Pencil size={14} />
                {zh ? '编辑' : 'Edit'}
              </button>
            )}
            {!readOnly && (
              <button
                aria-label={zh ? '移除这张卡片' : 'Remove this card'}
                onClick={() => {
                  onChange({
                    data: JSON.stringify({
                      ...payload,
                      cards: cards.filter((_, i) => i !== index),
                    }),
                  })
                  setIndex(i => Math.max(0, i - 1))
                  setFlipped(false)
                }}
              >
                <Trash2 size={14} />
              </button>
            )}
            <button
              disabled={index >= cards.length - 1}
              onClick={() => {
                setIndex(i => i + 1)
                setFlipped(false)
              }}
              aria-label={zh ? '下一张' : 'Next card'}
            >
              <ChevronRight size={16} />
            </button>
          </footer>
          <p className="dt-note-card-caption">
            {zh
              ? '翻看不计入掌握度。到期卡片会出现在「今日学习」。'
              : 'Browsing does not mark mastery. Due cards appear in Today.'}
          </p>
        </>
      ) : (
        !readOnly && (
          <button className="dt-note-card-empty" onClick={create}>
            <Plus size={18} />
            {zh ? '写下第一张卡片' : 'Write your first card'}
          </button>
        )
      )}
    </section>
  )
}
