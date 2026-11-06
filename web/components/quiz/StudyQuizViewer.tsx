'use client'
import dynamic from 'next/dynamic'
const QuestionFamilyReview = dynamic(() => import('./QuestionFamilyReview'), { ssr: false })

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Eye,
  ImagePlus,
  Loader2,
  RotateCcw,
  X,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import MarkdownRenderer from '@/components/common/MarkdownRenderer'
import { useChatStateAdapter } from '@/features/chat/ChatStateAdapter'
import { apiFetch } from '@/lib/api'
import { scopedUrl, activeWorkspaceId } from '@/lib/workspace-scope'
import { readWorkspaceDraft, saveWorkspaceDraft } from '@/lib/workspace-drafts'
import { randomUuid } from '@/lib/random-uuid'
import type { QuizQuestion as FullQuizQuestion } from '@/lib/quiz-types'
type QuizQuestion = Omit<FullQuizQuestion, 'correct_answer' | 'explanation'> &
  Partial<Pick<FullQuizQuestion, 'correct_answer' | 'explanation'>>

type Receipt = import('@/contracts/generated/api').components['schemas']['QuizReceipt']
type Draft = {
  answer: string
  images?: { id: string; filename: string; base64: string; mime_type: string }[]
  attemptId: string
  assistance: 'independent' | 'hint' | 'revealed'
  receipt?: Receipt
  submitted?: boolean
}

async function request<T>(url: string, payload?: unknown): Promise<T> {
  const response = await apiFetch(
    url,
    payload === undefined
      ? undefined
      : {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        },
  )
  const body = await response.json()
  if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`)
  return body as T
}

export default function StudyQuizViewer({
  questions,
  sessionId,
  turnId,
}: {
  questions: QuizQuestion[]
  sessionId: string
  turnId: string
}) {
  const { t } = useTranslation()
  const [index, setIndex] = useState(0)
  const current = questions[Math.min(index, questions.length - 1)]
  if (!current) return null
  return (
    <section
      className="study-quiz rounded-2xl border border-[var(--border)] bg-[var(--card)] overflow-hidden"
      aria-label={t('Practice')}
    >
      <header className="flex items-center gap-3 border-b border-[var(--border)] px-5 py-3">
        <span className="text-[11px] font-semibold tracking-wider text-[var(--primary)]">
          {t('Practice')}
        </span>
        <div className="flex flex-1 gap-1.5 overflow-x-auto">
          {questions.map((q, i) => (
            <button
              key={q.question_id || i}
              type="button"
              aria-label={t('Question {{number}}', { number: i + 1 })}
              aria-current={index === i ? 'step' : undefined}
              onClick={() => setIndex(i)}
              className={`size-7 shrink-0 rounded-full text-xs transition ${index === i ? 'bg-[var(--primary)] text-[var(--primary-foreground)]' : 'bg-[var(--muted)] text-[var(--muted-foreground)]'}`}
            >
              {q.answerable === false ? '!' : i + 1}
            </button>
          ))}
        </div>
        <button
          aria-label={t('Previous')}
          disabled={index === 0}
          onClick={() => setIndex((i) => i - 1)}
          className="p-1 disabled:opacity-30"
        >
          <ChevronLeft size={16} />
        </button>
        <button
          aria-label={t('Next')}
          disabled={index >= questions.length - 1}
          onClick={() => setIndex((i) => i + 1)}
          className="p-1 disabled:opacity-30"
        >
          <ChevronRight size={16} />
        </button>
      </header>
      {/* Keep every card mounted: switching the material or question preserves
        its in-flight request and image/draft state. */}
      {questions.map((q, i) => (
        <div
          key={`${sessionId}:${turnId}:${q.question_id}:${q.question_version || 'legacy'}`}
          hidden={i !== index}
        >
          <QuestionCard question={q} sessionId={sessionId} turnId={turnId} />
        </div>
      ))}
    </section>
  )
}

function QuestionCard({
  question: q,
  sessionId,
  turnId,
}: {
  question: QuizQuestion
  sessionId: string
  turnId: string
}) {
  const { t, i18n } = useTranslation()
  const { sendMessage, state } = useChatStateAdapter()
  const [workspace] = useState(activeWorkspaceId)
  const [draft, setDraft] = useState<Draft>(() => ({
    answer: '',
    attemptId: randomUuid(),
    assistance: 'independent',
  }))
  const draftRef = useRef(draft)
  const [ready, setReady] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [reference, setReference] = useState<Partial<QuizQuestion>>({})
  const mounted = useRef(true)
  const pending = useRef<Promise<void>>(Promise.resolve())
  const location = `/quiz/${encodeURIComponent(sessionId)}/${encodeURIComponent(turnId)}/${encodeURIComponent(q.question_id)}/${q.question_version || 'legacy'}`
  const endpoint = useCallback(
    (suffix: string) => scopedUrl(`/api/quiz${suffix}`, workspace),
    [workspace],
  )
  const statusUrl = useCallback(
    (id: string) =>
      endpoint(`/attempts/${encodeURIComponent(sessionId)}/${encodeURIComponent(id)}`),
    [endpoint, sessionId],
  )
  const update = useCallback(
    (patch: Partial<Draft>) => {
      const next = { ...draftRef.current, ...patch }
      draftRef.current = next
      setDraft(next)
      pending.current = pending.current
        .catch(() => {})
        .then(() =>
          saveWorkspaceDraft({ text: JSON.stringify(next), attachments: [] }, workspace, location),
        )
      void pending.current.catch(() => {
        if (mounted.current) setError(t('Draft could not be saved. Keep this page open.'))
      })
    },
    [location, t, workspace],
  )

  async function expose(level: 'hint' | 'revealed') {
    setBusy(true)
    try {
      const disclosed = await request<Partial<QuizQuestion>>(endpoint('/exposures'), {
        session_id: sessionId,
        turn_id: turnId,
        question_id: q.question_id,
        question_version: q.question_version || '',
        level,
        event_id: randomUuid(),
      })
      if (level === 'revealed') setReference(disclosed)
      update({ assistance: level })
      if (level === 'hint')
        void sendMessage(
          t(
            'Give me one hint for {{question}}. Do not reveal the answer. My current idea: {{answer}}',
            { question: q.question, answer: draftRef.current.answer },
          ),
          undefined,
          undefined,
          undefined,
          undefined,
          { capability: 'chat' },
        )
    } catch (error) {
      setError(error instanceof Error ? error.message : '无法保存提示记录，请重试。')
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    mounted.current = true
    let cancelled = false
    void readWorkspaceDraft(workspace, location)
      .then((saved) => {
        if (cancelled) return
        if (saved?.text) {
          const value = JSON.parse(saved.text) as Draft
          if (typeof value.answer === 'string' && typeof value.attemptId === 'string') {
            draftRef.current = value
            setDraft(value)
            if (value.assistance === 'revealed')
              void request<Partial<QuizQuestion>>(endpoint('/exposures'), {
                session_id: sessionId,
                turn_id: turnId,
                question_id: q.question_id,
                question_version: q.question_version || '',
                level: 'revealed',
                event_id: 'resume:' + value.attemptId,
              })
                .then((answer) => {
                  if (!cancelled) setReference(answer)
                })
                .catch(() => {
                  if (!cancelled) setError('无法读取复盘内容，请重新点击看答案。')
                })
            if (value.submitted)
              void request<Receipt>(statusUrl(value.attemptId))
                .then((receipt) => {
                  if (!cancelled) update({ receipt })
                })
                .catch(() => {
                  if (!cancelled) setError(t('Check the saved submission before retrying.'))
                })
          }
        }
      })
      .catch(() => {
        if (!cancelled) setError(t('Draft could not be restored. Keep this page open.'))
      })
      .finally(() => {
        if (!cancelled) setReady(true)
      })
    const flush = (event: Event) =>
      (event as CustomEvent<Promise<void>[]>).detail.push(pending.current)
    window.addEventListener('deeptutor:before-workspace-switch', flush)
    return () => {
      cancelled = true
      mounted.current = false
      window.removeEventListener('deeptutor:before-workspace-switch', flush)
    }
  }, [
    endpoint,
    location,
    q.question_id,
    q.question_version,
    sessionId,
    statusUrl,
    t,
    turnId,
    update,
    workspace,
  ])

  useEffect(() => {
    if (draft.receipt?.state !== 'grading') return
    const timer = window.setTimeout(() => {
      void request<Receipt>(statusUrl(draft.attemptId))
        .then((receipt) => {
          if (mounted.current) update({ receipt })
        })
        .catch(() => {})
    }, 3000)
    return () => window.clearTimeout(timer)
  }, [draft.attemptId, draft.receipt, statusUrl, update])

  const offline = async () => {
    try {
      const { saveQuizOffline } = await import('@/lib/offline-study')
      const href = await saveQuizOffline(
        workspace,
        {
          session_id: sessionId,
          turn_id: turnId,
          question_id: q.question_id,
          question_version: q.question_version || '',
        },
        { question: q.question, options: q.options, question_type: q.question_type },
      )
      window.open(href, '_blank', 'noopener')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }
  const submit = async () => {
    if (busy || !ready || (!draft.answer.trim() && !draft.images?.length) || q.answerable === false)
      return
    setBusy(true)
    setError('')
    const submission = { ...draftRef.current }
    try {
      update({ submitted: true })
      await pending.current // Persist identity before the first network byte.
      if (submission.submitted) {
        const prior = await request<Receipt>(statusUrl(submission.attemptId)).catch(() => null)
        if (prior && ['committed', 'cancelled', 'grading'].includes(prior.state)) {
          if (mounted.current) update({ receipt: prior })
          return
        }
      }
      const receipt = await request<Receipt>(endpoint('/submit'), {
        session_id: sessionId,
        turn_id: turnId,
        question_id: q.question_id,
        question_version: q.question_version || '',
        attempt_id: submission.attemptId,
        answer: submission.answer,
        answer_images: submission.images || [],
        assistance: submission.assistance,
        retry: Boolean(submission.submitted),
      })
      if (mounted.current) update({ receipt })
    } catch (reason) {
      const receipt = await request<Receipt>(statusUrl(submission.attemptId)).catch(() => null)
      if (mounted.current) {
        if (receipt) update({ receipt })
        else
          setError(
            reason instanceof Error
              ? reason.message
              : t('Assessment unavailable. Your answer is saved.'),
          )
      }
    } finally {
      if (mounted.current) setBusy(false)
    }
  }
  const cancel = async () => {
    try {
      const receipt = await request<Receipt>(`${statusUrl(draft.attemptId)}/cancel`, {})
      if (mounted.current) update({ receipt })
    } catch {
      if (mounted.current)
        setError(t('Cancellation has not been confirmed. Check the submission again.'))
    }
  }
  const attachImages = async (files: FileList | null) => {
    if (!files) return
    const selected = [...files]
    if (
      (draftRef.current.images?.length || 0) + selected.length > 4 ||
      selected.some(
        (file) =>
          file.size > 4 * 1024 * 1024 ||
          !['image/jpeg', 'image/png', 'image/webp', 'image/gif'].includes(file.type),
      )
    ) {
      setError(t('Attach up to four images, at most 4 MB each.'))
      return
    }
    try {
      const images = await Promise.all(
        selected.map(
          (file) =>
            new Promise<NonNullable<Draft['images']>[number]>((resolve, reject) => {
              const reader = new FileReader()
              reader.onload = () =>
                resolve({
                  id: randomUuid(),
                  filename: file.name,
                  mime_type: file.type,
                  base64: String(reader.result).split(',')[1],
                })
              reader.onerror = () => reject(new Error(t('Image could not be read.')))
              reader.readAsDataURL(file)
            }),
        ),
      )
      if (mounted.current) update({ images: [...(draftRef.current.images || []), ...images] })
    } catch {
      if (mounted.current) setError(t('Image could not be read.'))
    }
  }
  const retryQuestion = () => {
    const scope = q.source_scope || {}
    const content = t('Retry only this failed question: {{question}}', { question: q.question })
    void sendMessage(content, undefined, undefined, undefined, undefined, {
      capability: 'deep_question',
      requestSnapshotOverride: {
        content,
        capability: 'deep_question',
        capabilityOnce: true,
        workspaceId: workspace,
        enabledTools: [...state.enabledTools],
        language: i18n.resolvedLanguage || 'en',
        knowledgeBases: Array.isArray(scope.knowledge_bases)
          ? scope.knowledge_bases.filter((v): v is string => typeof v === 'string')
          : [],
        config: { num_questions: 1 },
        ...(typeof scope.reading_material_id === 'string'
          ? {
              readingMaterialId: scope.reading_material_id,
              workspaceMode: 'immersive_reading' as const,
            }
          : {}),
        ...(typeof scope.reading_material_revision === 'number'
          ? { readingMaterialRevision: scope.reading_material_revision }
          : {}),
        readingTurnFields: scope as NonNullable<
          import('@/features/chat/ChatStateAdapter').MessageRequestSnapshot['readingTurnFields']
        >,
      },
    })
  }
  const failed =
    q.answerable === false || q.generation_status === 'failed' || Boolean(q.issues?.length)
  const receipt = draft.receipt
  const grading = busy || receipt?.state === 'grading'
  const labels = {
    correct: t('Correct'),
    partial: t('Partially correct'),
    incorrect: t('Incorrect'),
    ungraded: t('Ungraded'),
  }
  return (
    <div className="space-y-4 p-5 sm:p-6">
      {failed ? (
        <div role="status" className="rounded-xl bg-amber-500/10 p-4 text-sm">
          <p className="font-semibold">{t('Question generation failed')}</p>
          <p className="mt-2 text-[var(--muted-foreground)]">
            {q.issues
              ?.map((issue) => t(`quiz.issue.${issue}`, { defaultValue: issue }))
              .join(' · ')}
          </p>
          <button
            type="button"
            disabled={state.isStreaming}
            onClick={retryQuestion}
            className="mt-3 inline-flex items-center gap-2 rounded-lg border border-[var(--border)] px-3 py-2"
          >
            <RotateCcw size={14} />
            {t('Retry this question')}
          </button>
        </div>
      ) : (
        <>
          <MarkdownRenderer content={q.question} />
          {q.options && Object.keys(q.options).length > 0 ? (
            <fieldset disabled={!ready || Boolean(draft.submitted)} className="grid gap-2">
              <legend className="sr-only">{t('Your answer')}</legend>
              {Object.entries(q.options).map(([key, text]) => (
                <label
                  key={key}
                  className={`flex cursor-pointer gap-3 rounded-xl border p-3 text-sm ${draft.answer === key ? 'border-[var(--primary)] bg-[var(--accent)]' : 'border-[var(--border)]'}`}
                >
                  <input
                    type="radio"
                    name={location}
                    value={key}
                    checked={draft.answer === key}
                    onChange={() => update({ answer: key })}
                  />
                  <span className="font-medium">{key}</span>
                  <MarkdownRenderer content={text} />
                </label>
              ))}
            </fieldset>
          ) : (
            <textarea
              value={draft.answer}
              disabled={!ready || Boolean(draft.submitted)}
              onChange={(event) => update({ answer: event.target.value })}
              aria-label={t('Your answer')}
              placeholder={t('Write your answer. Your draft is saved automatically.')}
              rows={4}
              className="w-full resize-y rounded-xl border border-[var(--border)] bg-[var(--background)] p-4 text-sm outline-none focus:ring-2 focus:ring-[var(--primary)]/30 disabled:opacity-70"
            />
          )}
          <div className="flex flex-wrap items-center gap-2 text-xs">
            {!draft.submitted && (
              <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-[var(--border)] px-3 py-2">
                <ImagePlus size={14} />
                {t('Attach answer images')}
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/webp,image/gif"
                  multiple
                  disabled={!ready || Boolean(draft.submitted)}
                  className="sr-only"
                  onChange={(event) => {
                    void attachImages(event.target.files)
                    event.target.value = ''
                  }}
                />
              </label>
            )}
            {(draft.images || []).map((img) => (
              <span
                key={img.id}
                className="flex items-center gap-2 rounded-lg bg-[var(--muted)] px-3 py-2"
              >
                {img.filename}
                {!draft.submitted && (
                  <button
                    type="button"
                    aria-label={t('Remove image')}
                    onClick={() =>
                      update({ images: draft.images?.filter((other) => other.id !== img.id) })
                    }
                  >
                    <X size={12} />
                  </button>
                )}
              </span>
            ))}
          </div>
          {error && (
            <p role="alert" className="text-sm text-amber-700 dark:text-amber-300">
              {error}
            </p>
          )}
          {receipt && (
            <div
              role="status"
              className="rounded-xl border border-[var(--border)] bg-[var(--secondary)] p-4 text-sm"
            >
              <p className="flex items-center gap-2 font-semibold">
                {receipt.assessment_committed && <Check size={15} />}
                {receipt.state === 'grading'
                  ? t('Grading your answer')
                  : receipt.state === 'cancelled'
                    ? t('Stopped')
                    : labels[receipt.result || 'ungraded']}
              </p>
              {receipt.evidence_kind && (
                <p className="text-xs text-[var(--muted-foreground)]">
                  {
                    {
                      independent: '独立作答',
                      assisted: '提示后作答',
                      correction: '查看反馈后的订正',
                      delayed_transfer: '延迟迁移练习',
                      unknown: '历史记录：独立性未知',
                    }[receipt.evidence_kind]
                  }
                </p>
              )}
              {receipt.feedback && (
                <div className="mt-2">
                  <MarkdownRenderer content={receipt.feedback} />
                </div>
              )}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2">
            {!receipt?.assessment_committed && receipt?.state !== 'cancelled' && (
              <button
                type="button"
                disabled={!ready || grading || (!draft.answer.trim() && !draft.images?.length)}
                onClick={() => void submit()}
                className="inline-flex items-center gap-2 rounded-xl bg-[var(--primary)] px-4 py-2.5 text-sm font-medium text-[var(--primary-foreground)] disabled:opacity-40"
              >
                {grading && <Loader2 size={15} className="animate-spin" />}
                {draft.submitted ? t('Retry grading') : t('Submit answer')}
              </button>
            )}
            {grading && (
              <button
                type="button"
                onClick={() => void cancel()}
                className="inline-flex gap-2 px-3 py-2 text-sm"
              >
                <X size={15} />
                {t('Request cancellation')}
              </button>
            )}
            {draft.submitted && !grading && (
              <button
                type="button"
                onClick={() => {
                  update({
                    answer: '',
                    images: [],
                    attemptId: randomUuid(),
                    assistance: 'independent',
                    submitted: false,
                    receipt: undefined,
                  })
                  setError('')
                }}
                className="inline-flex items-center gap-2 px-3 py-2 text-sm"
              >
                <RotateCcw size={14} />
                {t('New attempt')}
              </button>
            )}
            {!draft.submitted && (
              <button
                type="button"
                disabled={busy || !ready}
                onClick={() => void expose('revealed')}
                className="inline-flex items-center gap-2 px-3 py-2 text-sm text-[var(--muted-foreground)]"
              >
                <Eye size={14} />
                {t('Show reference')}
              </button>
            )}
            {!draft.submitted && draft.assistance !== 'revealed' && (
              <button
                type="button"
                disabled={state.isStreaming || !ready}
                onClick={() => void expose('hint')}
                className="px-3 py-2 text-sm text-[var(--primary)]"
              >
                {t('Get a hint')}
              </button>
            )}
            {receipt?.assessment_committed && (
              <button
                type="button"
                disabled={state.isStreaming}
                onClick={() =>
                  void sendMessage(
                    t(
                      'Help me understand my answer to {{question}}. My answer: {{answer}}. Feedback: {{feedback}}',
                      { question: q.question, answer: draft.answer, feedback: receipt.feedback },
                    ),
                    undefined,
                    undefined,
                    undefined,
                    undefined,
                    { capability: 'chat' },
                  )
                }
                className="px-3 py-2 text-sm text-[var(--primary)]"
              >
                {t('Continue learning')}
              </button>
            )}
          </div>
          {q.question_version && (
            <button
              type="button"
              className="px-3 py-2 text-sm text-[var(--primary)]"
              onClick={() => void offline()}
            >
              {t('保存供离线使用')}
            </button>
          )}
          {(receipt?.assessment_committed || draft.assistance === 'revealed') &&
            q.question_version && (
              <QuestionFamilyReview
                endpoint={endpoint}
                identity={{
                  session_id: sessionId,
                  turn_id: turnId,
                  question_id: q.question_id,
                  question_version: q.question_version,
                }}
                onDisclosure={() => update({ assistance: 'revealed' })}
              />
            )}
          {draft.assistance === 'revealed' && (
            <div className="rounded-xl bg-[var(--muted)] p-4 text-sm">
              <MarkdownRenderer
                content={`${reference.correct_answer || q.correct_answer || ''}\n\n${reference.explanation || q.explanation || ''}`}
              />
            </div>
          )}
        </>
      )}
    </div>
  )
}
