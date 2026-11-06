'use client'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import { scopedUrl } from '@/lib/workspace-scope'
import { useActiveWorkspaceId } from '@/hooks/useActiveWorkspaceId'
import type { NoteReviewCard as Card } from '@/lib/personal-study'
import './note-library.css'
import { FileDown, Layers, X } from 'lucide-react'
import { exportNoteFlashcards, noteRequest } from '@/lib/notes-api'
import PickerShell from '@/components/common/PickerShell'
export default function NoteReview() {
  const workspace = useActiveWorkspaceId()
  const params = useSearchParams()
  return (
    <ScopedNoteReview
      key={`${workspace}:${params.get('card') || ''}:${params.get('page') || ''}`}
    />
  )
}
function ScopedNoteReview() {
  const { t, i18n } = useTranslation()
  const zh = i18n.language.startsWith('zh')
  const workspace = useActiveWorkspaceId()
  const params = useSearchParams()
  const target = params.get('card'),
    targetPage = params.get('page')
  const autoOpen = params.get('review') === '1'
  const [cards, setCards] = useState<Card[]>([])
  const [open, setOpen] = useState(false)
  const [revealed, setRevealed] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const pending = useRef(false)
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  useEffect(() => {
    let active = true
    setCards([])
    setRevealed(false)
    setOpen(autoOpen)
    void noteRequest<{ cards: Card[] }>('/review/due')
      .then(r => {
        if (!active) return
        const match = r.cards.find(c => c.card_id === target && c.page_id === targetPage)
        setCards(match ? [match, ...r.cards.filter(c => c !== match)] : r.cards)
      })
      .catch(e => {
        if (active) setError(String(e))
      })
    return () => {
      active = false
    }
  }, [workspace, autoOpen, target, targetPage])
  const card = cards[0]
  const exportAll = async () => {
    if (pending.current) return
    pending.current = true
    setBusy(true)
    try {
      const blob = await exportNoteFlashcards()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'deeptutor-flashcards.txt'
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (err) {
      if (mounted.current) setError(String(err))
    } finally {
      pending.current = false
      if (mounted.current) setBusy(false)
    }
  }
  const grade = async (remembered: boolean) => {
    if (pending.current) return
    pending.current = true
    setBusy(true)
    try {
      await noteRequest(`/${card.page_id}/review`, {
        card_id: card.card_id,
        remembered,
        expected_content_hash: card.content_hash,
      })
      if (!mounted.current) return
      setCards(c => c.slice(1))
      setRevealed(false)
    } catch (err) {
      if (mounted.current) setError(String(err))
    } finally {
      pending.current = false
      if (mounted.current) setBusy(false)
    }
  }
  const decide = async (action: 'keep' | 'reset') => {
    if (pending.current) return
    pending.current = true
    setBusy(true)
    setError('')
    try {
      await noteRequest(`/${card.page_id}/review/schedule`, {
        card_id: card.card_id,
        expected_content_hash: card.content_hash,
        action,
      })
      if (!mounted.current) return
      if (action === 'keep' && card.due_at > Date.now() / 1000) setCards(c => c.slice(1))
      else
        setCards(c =>
          c.map((item, index) => (index === 0 ? { ...item, needs_schedule_review: false } : item))
        )
      setRevealed(false)
    } catch (err) {
      if (mounted.current) setError(String(err))
    } finally {
      pending.current = false
      if (mounted.current) setBusy(false)
    }
  }
  if (!cards.length && !open && !error) return null
  return (
    <>
      <button
        className="dt-notes-review-trigger"
        onClick={() => {
          setRevealed(false)
          setOpen(true)
        }}
      >
        <Layers size={14} strokeWidth={1.7} />
        {t('Review flashcards')}
        <span className="dt-notes-review-count">{cards.length}</span>
      </button>
      <PickerShell open={open} onClose={() => setOpen(false)} ariaLabel={t('Review flashcards')}>
        <div className="dt-notes-review">
          <header>
            <span className="dt-notes-review-heading">
              {t('Review flashcards')}
              <button
                type="button"
                className="dt-notes-review-export"
                disabled={busy}
                onClick={() => void exportAll()}
              >
                <FileDown size={13} strokeWidth={1.7} />
                {t('Export all for Anki')}
              </button>
            </span>
            {card && <span>{t('{{count}} left', { count: cards.length })}</span>}
            <button
              className="dt-note-icon-button"
              aria-label={t('Close')}
              onClick={() => setOpen(false)}
            >
              <X size={15} />
            </button>
          </header>
          {card ? (
            <>
              <div className="dt-notes-review-card">
                <p className="is-front">{card.front}</p>
                {revealed && <p className="is-back">{card.back}</p>}
              </div>
              <Link
                href={scopedUrl(`/co-writer/${card.page_id}`, workspace) + `#${card.block_id}`}
                className="dt-notes-review-source"
              >
                {t('From “{{page}}”', { page: card.page_title || t('Untitled') })} ↗
              </Link>
              {card.needs_schedule_review && (
                <div className="dt-notes-card-change">
                  <strong>
                    {zh
                      ? '内容已更新 · 复习进度由你决定'
                      : 'Content changed · choose your schedule'}
                  </strong>
                  {card.previous_content && (
                    <div>
                      <small>{zh ? '修改前' : 'Previous'}</small>
                      <p>{card.previous_content.front}</p>
                      <p>{card.previous_content.back}</p>
                    </div>
                  )}
                  <div>
                    <small>{zh ? '修改后' : 'Current'}</small>
                    <p>{card.front}</p>
                    <p>{card.back}</p>
                  </div>
                  <p>
                    {zh
                      ? '保留排期会沿用当前复习间隔；重新学习会让卡片立即进入复习。'
                      : 'Keep the current interval, or reset it to review the updated card now.'}
                  </p>
                  <div className="dt-notes-review-actions">
                    <button disabled={busy} onClick={() => void decide('keep')}>
                      {zh ? '保留排期' : 'Keep schedule'}
                    </button>
                    <button
                      disabled={busy}
                      className="is-primary"
                      onClick={() => void decide('reset')}
                    >
                      {zh ? '重新学习' : 'Learn again'}
                    </button>
                  </div>
                </div>
              )}
              {!card.needs_schedule_review && (
                <div className="dt-notes-review-actions">
                  {revealed ? (
                    <>
                      <button disabled={busy} onClick={() => void grade(false)}>
                        {t('Again')}
                      </button>
                      <button
                        disabled={busy}
                        className="is-primary"
                        onClick={() => void grade(true)}
                      >
                        {t('Remembered')}
                      </button>
                    </>
                  ) : (
                    <button className="is-primary" onClick={() => setRevealed(true)}>
                      {t('Show answer')}
                    </button>
                  )}
                </div>
              )}
            </>
          ) : (
            <div className="dt-notes-review-done">
              <span aria-hidden>🎉</span>
              <p>{t('All caught up')}</p>
            </div>
          )}
          {error && (
            <p role="alert" className="dt-notes-review-error">
              {error}
            </p>
          )}
        </div>
      </PickerShell>
    </>
  )
}
