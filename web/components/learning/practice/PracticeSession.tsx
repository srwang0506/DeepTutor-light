'use client'

import Link from 'next/link'
import Markdown from '@/components/common/MarkdownRenderer'
import { ObjectiveBinding } from './ObjectiveBinding'
import { practiceMarkdown } from '@/lib/practice-content'
import { getLocale } from '@/lib/datetime'
import { setPendingPrompt } from '@/lib/pending-prompt'
import { randomUuid } from '@/lib/random-uuid'
import { activeWorkspaceId, scopedUrl } from '@/lib/workspace-scope'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, CheckCircle2, Loader2 } from 'lucide-react'
import {
  checkPracticeAnswer,
  getPracticeQuestion,
  PracticeRequestError,
  savePracticeReview,
  type PracticeQuestion,
  type PracticeRef,
  type PracticeRating,
  type PracticeReviewReceipt,
} from '@/lib/practice-api'

const RATINGS: { value: PracticeRating; label: string; description: string }[] = [
  { value: 'again', label: 'Again', description: 'Practice again this round' },
  { value: 'hard', label: 'Hard', description: 'I needed a hint' },
  { value: 'good', label: 'Recalled', description: 'Review in a few days' },
  { value: 'easy', label: 'Easy', description: 'I knew it confidently' },
]

type SavedAttempt = {
  receipt: PracticeReviewReceipt
  answer: string
  question: PracticeQuestion['entry']
  workspace: string
}

export function PracticeSession({
  ids,
  questions,
  onClose,
  backLabel,
}: {
  ids?: number[]
  questions?: PracticeRef[]
  onClose: () => void
  backLabel?: string
}) {
  const [queue, setQueue] = useState<PracticeRef[]>(
    () => questions ?? (ids ?? []).map((id) => ({ id })),
  )
  const [total] = useState(queue.length)
  const [skipped, setSkipped] = useState(0)
  const { t, i18n } = useTranslation()
  const [index, setIndex] = useState(0)
  const [saved, setSaved] = useState(0)
  const [reviewCount, setReviewCount] = useState(0)
  const [saving, setSaving] = useState(false)
  const [nextDue, setNextDue] = useState<number | null>(null)
  const [recent, setRecent] = useState<SavedAttempt[]>([])
  const dueDates = useRef(new Map<string, number>())
  const complete = index >= queue.length
  return (
    <section className="mx-auto max-w-3xl">
      <div className="mb-5 flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={onClose}
          disabled={saving}
          className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft size={15} />
          {backLabel ?? t('Back to practice')}
        </button>
        <p className="text-xs text-muted-foreground" aria-live="polite">
          {t('{{count}} remaining', { count: total - saved - skipped })}
        </p>
      </div>
      <progress
        max={total || 1}
        value={saved}
        aria-label={t('Practice progress')}
        className="mb-6 h-1.5 w-full accent-primary"
      />
      {complete ? (
        <div className="rounded-2xl border border-border bg-card px-6 py-12 text-center">
          <CheckCircle2 className="mx-auto mb-4 text-emerald-600" size={32} />
          <h2 className="font-serif text-2xl font-semibold">{t('Practice complete')}</h2>
          <p className="mt-3 text-sm text-muted-foreground">
            {t('{{count}} reviews saved. Your next review dates have been updated.', {
              count: reviewCount,
            })}
          </p>
          {skipped > 0 && (
            <p className="mt-2 text-sm text-muted-foreground">
              {t('{{count}} skipped questions remain due.', { count: skipped })}
            </p>
          )}
          {nextDue && (
            <p className="mt-2 text-xs text-muted-foreground">
              {t('Next review: {{date}}', {
                date: new Date(nextDue * 1000).toLocaleDateString(
                  getLocale(i18n.resolvedLanguage || i18n.language),
                ),
              })}
            </p>
          )}
          {recent.length > 0 && (
            <div className="mx-auto mt-6 max-w-xl space-y-2 text-left">
              {recent.map((attempt) => (
                <AttemptFollowup key={attempt.receipt.attempt_id} attempt={attempt} />
              ))}
            </div>
          )}
          <button
            type="button"
            onClick={onClose}
            className="mt-6 rounded-xl bg-primary px-5 py-2.5 text-sm text-primary-foreground"
          >
            {backLabel ?? t('Back to practice')}
          </button>
        </div>
      ) : (
        <>
          <PracticeTurn
            key={`${index}:${queue[index].content_workspace_id}:${queue[index].id}`}
            id={queue[index].id}
            workspaceId={queue[index].content_workspace_id}
            onBusy={setSaving}
            onSaved={(attempt) => {
              setReviewCount((value) => value + 1)
              const { due_at: due, rating } = attempt.receipt
              if (rating === 'again') setQueue((current) => [...current, current[index]])
              else setSaved((value) => value + 1)
              if (attempt.receipt.attempt_id) {
                setRecent((previous) => [attempt, ...previous].slice(0, 3))
              }
              if (due !== null) {
                dueDates.current.set(
                  JSON.stringify([queue[index].content_workspace_id, queue[index].id]),
                  due,
                )
                setNextDue(Math.min(...dueDates.current.values()))
              }
              setIndex((value) => value + 1)
            }}
            onSkip={() => {
              setSkipped((value) => value + 1)
              setIndex((value) => value + 1)
            }}
          />
          {recent[0] && (
            <div className="mt-4">
              <AttemptFollowup attempt={recent[0]} />
            </div>
          )}
        </>
      )}
    </section>
  )
}

function AttemptFollowup({ attempt }: { attempt: SavedAttempt }) {
  const { t } = useTranslation()
  const { receipt, question, answer, workspace } = attempt
  const source = question.session_id.startsWith('studio-')
    ? scopedUrl(`/objects/${encodeURIComponent(question.session_id.slice(7))}`, workspace)
    : question.session_id
      ? scopedUrl(`/chat/${encodeURIComponent(question.session_id)}`, workspace)
      : scopedUrl(`/learning/practice?question=${question.id}`, workspace)
  function prepare() {
    const outcome =
      receipt.correct === null
        ? t('Self-assessed or ungraded')
        : t(receipt.correct ? 'Correct' : 'Needs another try')
    setPendingPrompt(
      [
        t(
          'Help me understand this practice attempt. Identify the gap, then give me one similar question without revealing its solution.',
        ),
        `${t('Question')}: ${question.question}`,
        ...Object.entries(question.options || {}).map(([id, text]) => `${id}. ${text}`),
        `${t('My answer in this attempt')}: ${answer || t('No answer submitted')}`,
        `${t('Result')}: ${outcome}`,
        `${t('Original source')}: ${question.material_title || question.session_title || t('Question Bank')} · ${source}`,
        `${t('Practice attempt ID')}: ${receipt.attempt_id}`,
      ].join('\n\n'),
      `chat:${workspace}`,
    )
  }
  const evidence = receipt.objective_evidence
  return (
    <article className="rounded-xl border border-border bg-background px-4 py-3">
      <p className="line-clamp-2 text-sm font-medium">{question.question}</p>
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs">
        <span className="text-muted-foreground">
          {evidence === 'applied'
            ? t('Recall evidence added to your learning objective')
            : evidence === 'pending'
              ? t('Review saved; objective evidence is waiting to sync')
              : evidence === 'unavailable'
                ? t('Review saved; linked learning objective is unavailable')
                : receipt.independent
                  ? t('No recorded assistance for this recall')
                  : t('Review saved')}
        </span>
        <Link
          href={scopedUrl('/chat', workspace)}
          onClick={prepare}
          className="font-medium text-primary underline-offset-4 hover:underline"
        >
          {t('Ask your tutor about this attempt')}
        </Link>
      </div>
    </article>
  )
}

function PracticeTurn({
  id,
  workspaceId,
  onSaved,
  onSkip,
  onBusy,
}: {
  id: number
  workspaceId?: string
  onSaved: (attempt: SavedAttempt) => void
  onSkip: () => void
  onBusy: (busy: boolean) => void
}) {
  const { t } = useTranslation()
  const [question, setQuestion] = useState<PracticeQuestion | null>(null)
  const [answer, setAnswer] = useState('')
  const [revealed, setRevealed] = useState(false)
  const [correct, setCorrect] = useState<boolean | null>(null)
  const [reference, setReference] = useState({ answer: '', explanation: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [conflict, setConflict] = useState(false)
  const [reload, setReload] = useState(0)
  const pending = useRef(false)
  const [submittedRating, setSubmittedRating] = useState<PracticeRating | null>(null)
  const reviewPending = submittedRating !== null
  const submission = useRef<{
    request_id: string
    version: number
    rating: PracticeRating
    answer: string
    self_report?: boolean
  } | null>(null)
  const checked = useRef<{ request_id: string; version: number; answer: string } | null>(null)
  useEffect(() => {
    let active = true
    void getPracticeQuestion(id, workspaceId)
      .then((result) => {
        if (active) setQuestion(result)
      })
      .catch((err) => {
        if (active) setError(String(err.message || err))
      })
    return () => {
      active = false
    }
  }, [id, reload, workspaceId])
  async function reveal() {
    if (pending.current || submission.current || !question || conflict || !answer.trim()) return
    pending.current = true
    setBusy(true)
    onBusy(true)
    setError('')
    try {
      if (
        !checked.current ||
        checked.current.answer !== answer ||
        checked.current.version !== question.state.version
      ) {
        checked.current = { request_id: randomUuid(), version: question.state.version, answer }
      }
      const { request_id, version } = checked.current
      const verdict = await checkPracticeAnswer(id, answer, workspaceId, { request_id, version })
      setCorrect(verdict.correct)
      setReference({
        answer: verdict.correct_answer ?? question.entry.correct_answer,
        explanation: verdict.explanation ?? question.entry.explanation,
      })
      setRevealed(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      if (err instanceof PracticeRequestError && err.status === 409) setConflict(true)
    } finally {
      pending.current = false
      setBusy(false)
      onBusy(false)
    }
  }
  async function rate(rating: PracticeRating, selfReport = false) {
    if (pending.current || !question || conflict) return
    pending.current = true
    setBusy(true)
    onBusy(true)
    setError('')
    // Retrying a lost response must reuse its ID and exact payload.
    submission.current ??= {
      request_id:
        !selfReport && checked.current?.answer === answer
          ? checked.current.request_id
          : randomUuid(),
      version: question.state.version,
      rating,
      answer,
      self_report: selfReport,
    }
    setSubmittedRating(submission.current.rating)
    try {
      const result = await savePracticeReview(id, submission.current, workspaceId)
      onSaved({
        receipt: result,
        answer: submission.current.answer,
        question: question.entry,
        workspace: workspaceId ?? activeWorkspaceId(),
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      if (err instanceof PracticeRequestError && err.status === 409) setConflict(true)
    } finally {
      pending.current = false
      setBusy(false)
      onBusy(false)
    }
  }
  function reset() {
    setQuestion(null)
    setAnswer('')
    setRevealed(false)
    setCorrect(null)
    setReference({ answer: '', explanation: '' })
    setError('')
    setConflict(false)
    submission.current = null
    checked.current = null
    setSubmittedRating(null)
    setReload((value) => value + 1)
  }
  const entry = question?.entry
  const multi =
    entry?.question_type === 'multi_choice' || entry?.question_type === 'multiple_select'
  const choices = Object.entries(entry?.options || {})
  return (
    <div className="rounded-2xl border border-border bg-card p-5 sm:p-8">
      {error && (
        <div role="alert" className="mb-4 rounded-lg bg-destructive/5 p-3 text-sm text-destructive">
          <p>{t(error)}</p>
          {(conflict || !entry) && (
            <button type="button" onClick={reset} className="mt-2 underline">
              {t('Reload question')}
            </button>
          )}
          {entry && reviewPending && !conflict && (
            <div className="mt-2 space-y-2">
              <p>{t('Your submitted answer is kept unchanged while its save is retried.')}</p>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  const retry = submission.current
                  if (retry) void rate(retry.rating, retry.self_report)
                }}
                className="underline disabled:opacity-50"
              >
                {t('Retry saving review')}
              </button>
            </div>
          )}
        </div>
      )}
      {!entry || !question ? (
        error ? (
          <button
            type="button"
            onClick={onSkip}
            className="text-sm text-muted-foreground hover:underline"
          >
            {t('Skip for now')}
          </button>
        ) : (
          <p role="status" className="py-8 text-sm text-muted-foreground">
            {t('Loading…')}
          </p>
        )
      ) : (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span>{entry.material_title || t('Practice')}</span>
            {entry.categories?.map((tag) => (
              <span key={tag.id} className="rounded-full bg-muted px-2 py-1">
                {tag.name}
              </span>
            ))}
          </div>
          {!revealed && (
            <ObjectiveBinding
              id={id}
              state={question.state}
              original={
                entry.source === 'mastery_path' &&
                !!entry.mastery_path_id &&
                !!entry.knowledge_point_id
              }
              workspace={workspaceId}
              disabled={busy || conflict || reviewPending}
              onSaved={(state) =>
                setQuestion((current) => (current ? { ...current, state } : current))
              }
              onBusy={(value) => {
                setBusy(value)
                onBusy(value)
              }}
              onConflict={() => setConflict(true)}
            />
          )}
          <div className="text-base leading-relaxed">
            <Markdown
              learningTextSize={16}
              content={practiceMarkdown(entry.question)}
              allowHtml={false}
              className="[overflow-wrap:anywhere] [&_pre]:max-w-full [&_pre]:overflow-x-auto"
            />
          </div>
          {multi && (
            <p className="mt-3 text-xs text-muted-foreground">{t('Select all correct answers')}</p>
          )}
          {choices.length > 0 ? (
            <fieldset disabled={revealed || busy || conflict || reviewPending} className="mt-6 grid gap-2">
              <legend className="sr-only">{t('Your answer')}</legend>
              {choices.map(([key, text]) => {
                const selected = multi ? answer.split(',').includes(key) : answer === key
                return (
                  <label
                    key={key}
                    className={`flex cursor-pointer items-start gap-3 rounded-xl border p-3 text-sm ${selected ? 'border-primary bg-primary/5' : 'border-border hover:bg-muted/40'}`}
                  >
                    <input
                      type={multi ? 'checkbox' : 'radio'}
                      name={`practice-${id}`}
                      checked={selected}
                      value={key}
                      onChange={() =>
                        setAnswer(
                          multi
                            ? (selected
                                ? answer.split(',').filter((value) => value !== key)
                                : [...answer.split(',').filter(Boolean), key]
                              )
                                .sort()
                                .join(',')
                            : key,
                        )
                      }
                      className="mt-1 accent-primary"
                    />
                    <span className="font-semibold">{key}</span>
                    <div className="min-w-0">
                      <Markdown
                        learningTextSize={16}
                        content={practiceMarkdown(text)}
                        allowHtml={false}
                      />
                    </div>
                  </label>
                )
              })}
            </fieldset>
          ) : (
            <label className="mt-6 block text-sm font-medium">
              {t('Your answer')}
              <textarea
                value={answer}
                onChange={(event) => setAnswer(event.target.value)}
                disabled={revealed || busy || conflict || reviewPending}
                maxLength={20000}
                rows={5}
                placeholder={t('Try recalling the answer before revealing it.')}
                className="mt-2 block w-full resize-y rounded-xl border border-border bg-background p-3 font-normal outline-none focus:border-primary"
              />
            </label>
          )}
          {!revealed ? (
            <div className="mt-5 flex flex-wrap items-center gap-3">
              <button
                type="button"
                disabled={busy || conflict || reviewPending || !answer.trim()}
                onClick={() => void reveal()}
                className="rounded-xl bg-primary px-4 py-2.5 text-sm text-primary-foreground disabled:opacity-50"
              >
                {busy && !reviewPending ? t('Checking…') : t('Check and reveal answer')}
              </button>
              <button
                type="button"
                disabled={busy || conflict || reviewPending}
                onClick={() => void rate('good', true)}
                className="rounded-xl border border-border px-4 py-2.5 text-sm hover:bg-muted"
              >
                {t('Skip recall and reschedule')}
              </button>
              <button
                type="button"
                onClick={onSkip}
                disabled={busy || reviewPending}
                className="text-sm text-muted-foreground hover:underline"
              >
                {t('Skip for now')}
              </button>
            </div>
          ) : (
            <div className="mt-6 space-y-5">
              <div className="rounded-xl bg-muted/50 p-4">
                <p className="mb-3 text-sm font-semibold">
                  {correct === null
                    ? t('Compare your answer with the reference')
                    : correct
                      ? t('Correct')
                      : t('Needs another try')}
                </p>
                <div className="text-sm">
                  <Markdown
                    learningTextSize={16}
                    content={
                      reference.answer ||
                      t(
                        'No reference answer was saved. Use the original source to check your work.',
                      )
                    }
                  />
                  {reference.explanation && (
                    <div className="mt-3 border-t border-border pt-3">
                      <Markdown learningTextSize={16} content={reference.explanation} />
                    </div>
                  )}
                </div>
              </div>
              {correct !== null ? (
                <div className="flex flex-wrap items-center justify-between gap-4">
                  <p className="text-xs text-muted-foreground">
                    {correct
                      ? t(
                          'This question leaves the current round and returns when its next review is due.',
                        )
                      : t('Practice again this round')}
                  </p>
                  <button
                    type="button"
                    disabled={busy || conflict || reviewPending}
                    onClick={() => void rate(correct ? 'good' : 'again')}
                    className="rounded-xl bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground disabled:opacity-40"
                  >
                    {correct ? t('Save recall · Next question') : t('Practice again')}
                  </button>
                </div>
              ) : (
                <div>
                  <h3 className="text-sm font-medium">{t('How well did you remember?')}</h3>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {t('A self-rating changes this question’s schedule without verifying mastery.')}
                  </p>
                  <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
                    {RATINGS.map((rating) => (
                      <button
                        key={rating.value}
                        aria-label={`${t(rating.label)} ${t(rating.description)}`}
                        type="button"
                        disabled={
                          busy ||
                          conflict ||
                          reviewPending
                        }
                        onClick={() => void rate(rating.value)}
                        className="rounded-xl border border-border p-3 text-left transition hover:border-primary hover:bg-primary/5 disabled:cursor-not-allowed disabled:opacity-35"
                      >
                        <span className="block text-sm font-semibold">{t(rating.label)}</span>
                        <span className="mt-1 block text-xs text-muted-foreground">
                          {t(rating.description)}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
          {busy && reviewPending && (
            <p role="status" className="mt-4 flex items-center gap-2 text-sm">
              <Loader2 size={15} className="animate-spin" />
              {t('Saving review…')}
            </p>
          )}
        </>
      )}
    </div>
  )
}
