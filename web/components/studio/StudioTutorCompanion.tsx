'use client'

import Link from 'next/link'
import dynamic from 'next/dynamic'
import { BookOpen, Link2, MessageCircle, Plus, X } from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type MouseEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { ChatMessageList } from '@/features/chat/messages'
import { useChatStateAdapter, type MessageRequestSnapshot } from '@/features/chat/ChatStateAdapter'
import { useChatAutoScroll } from '@/hooks/useChatAutoScroll'
import { useMeasuredHeight } from '@/hooks/useMeasuredHeight'
import { copyText } from '@/lib/clipboard'
import { getSession } from '@/lib/session-api'
import { shouldSubmitOnEnter } from '@/lib/composer-keyboard'
import { buildVisiblePath } from '@/lib/message-branches'
import { hasPendingAskUser } from '@/lib/ask-user-state'
import { recoveryPrincipal } from '@/lib/recovery-principal'
import { scopedUrl } from '@/lib/workspace-scope'
import { browserStorage } from '@/shared/storage'
import { Button } from '@/shared/ui/Button'
import { InlineAlert } from '@/shared/ui/InlineAlert'
import { normalizeStudioReferences, studioReferenceHref, studioReferenceKey, STUDIO_SOURCE_LIMIT, type StudioSourceItem, type StudioSourceReference } from '@/lib/studio-references'

const StudioReferencePicker = dynamic(() => import('@/components/chat/StudioReferencePicker'), { ssr: false })

interface SavedDiscussion { id: string; title: string }
type Binding = { status: 'loading' } | { status: 'error' } | { status: 'ready'; key: string; sessionId?: string }
export interface PaperReviewConversation {
  reference: StudioSourceReference
  sessionId: string
  mode: string
  allowInlineJump?: boolean
  onCheckpoint: () => void
  start: { nonce: string; text: string } | null
  onStartAttempted: () => void
}
const maxDiscussions = 20
const newDraftSlot = ''

function sessionHistoryKey(workspace: string, reference: StudioSourceReference) {
  const owner = recoveryPrincipal()
  return owner === null ? null
    : `deeptutor.paper-tutor:${JSON.stringify([owner, workspace, reference.scope, reference.id, reference.revision])}`
}

function readDiscussions(key: string | null): SavedDiscussion[] {
  if (!key) return []
  try {
    const rows: unknown = JSON.parse(browserStorage.readRaw('local', key) ?? '[]')
    return Array.isArray(rows)
      ? rows.filter(row => row && typeof row.id === 'string' && typeof row.title === 'string').slice(0, maxDiscussions)
      : []
  } catch { return [] }
}

export function discussionUsesEdition(
  messages: { role: string; metadata?: Record<string, unknown> }[],
  reference: StudioSourceReference,
) {
  const opening = messages.find(message => message.role === 'user')
  const snapshot = opening?.metadata?.request_snapshot
  if (!snapshot || typeof snapshot !== 'object') return false
  const sources = snapshot as Record<string, unknown>
  return normalizeStudioReferences(sources.studioReferences ?? sources.studio_references).some(
    source => source.kind === 'edition' && source.id === reference.id &&
      source.scope === reference.scope && source.revision === reference.revision,
  )
}

export function discussionUsesReview(
  messages: { role: string; metadata?: Record<string, unknown> }[],
  source: StudioSourceReference,
  review: StudioSourceReference,
) {
  const opening = messages.find(message => message.role === 'user')
  // An API-bound native session is created before its first user message.
  if (!opening) return true
  const snapshot = opening.metadata?.request_snapshot
  if (!snapshot || typeof snapshot !== 'object') return false
  const fields = snapshot as Record<string, unknown>
  const refs = normalizeStudioReferences(fields.studioReferences ?? fields.studio_references)
  return [source, review].every(expected => refs.some(ref =>
    ref.kind === expected.kind && ref.scope === expected.scope && ref.id === expected.id && ref.revision === expected.revision &&
    (expected.kind !== 'edition' || ref.locator?.blob_hash === expected.locator?.blob_hash),
  ))
}

export default function StudioTutorCompanion({
  workspace, reference, paperTitle, initialSessionId, prompt, onSessionBound, onJump, onClearSelection, onPractice, practiceBusy,
  active = true, review,
}: {
  workspace: string
  reference: StudioSourceReference
  paperTitle: string
  initialSessionId: string
  prompt: { text: string; nonce: number } | null
  onSessionBound: (id: string) => void
  onJump: (page: number, blobHash?: string) => void
  onClearSelection: () => void
  onPractice?: () => void
  practiceBusy: boolean
  active?: boolean
  review?: PaperReviewConversation
}) {
  const { t } = useTranslation()
  const {
    state, sendMessage, newSession, loadSession, showCachedSession,
    cancelStreamingTurn, regenerateLastMessage, resendLastMessage,
    retryFailedSubmission, discardFailedSubmission, switchBranch, submitUserReply,
  } = useChatStateAdapter()
  const historyKey = review ? null : sessionHistoryKey(workspace, reference)
  const [history, setHistory] = useState(() => readDiscussions(historyKey))
  const [target, setTarget] = useState(() => ({
    id: review?.sessionId || initialSessionId || readDiscussions(historyKey)[0]?.id || '', request: 0,
  }))
  const lastDiscussionId = useRef(target.id)
  const [binding, setBinding] = useState<Binding>({ status: 'loading' })
  // Scope unsent text to the discussion. Visiting another discussion does not
  // overwrite it, and presets only fill an empty composer.
  const drafts = useRef(new Map<string, string>())
  const [draft, setDraft] = useState('')
  const [retrying, setRetrying] = useState<{ parentMessageId: number | null; snapshot?: MessageRequestSnapshot } | null>(null)
  const slot = target.id || newDraftSlot
  const [submitError, setSubmitError] = useState(false)
  const [picker, setPicker] = useState(false)
  const [copied, setCopied] = useState(false)
  const [sources, setSources] = useState<StudioSourceItem[]>([])
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const submitLock = useRef(false)
  const alive = useRef(false)
  const knownIds = useRef(new Set(readDiscussions(historyKey).map(row => row.id)))
  const appliedPrompt = useRef<number | null>(null)
  const submissionBinding = useRef<{ binding: Binding; owns: boolean }>({ binding: { status: 'loading' }, owns: false })
  const { id: editionId, revision, scope } = reference
  const originalHash = reference.locator?.blob_hash
  const reviewId = review?.reference.id
  const reviewRevision = review?.reference.revision
  const reviewScope = review?.reference.scope
  const reviewSession = review?.sessionId

  useEffect(() => {
    alive.current = true
    return () => { alive.current = false }
  }, [])

  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    void Promise.resolve().then(async () => {
      if (controller.signal.aborted) return
      const discussionId = target.id || (!reviewId ? lastDiscussionId.current : '')
      if (!discussionId) {
        const key = newSession({ workspaceId: workspace || null, capability: 'chat',
          draftRecoveryScope: JSON.stringify(['paper', scope, editionId, revision]),
        })
        setBinding({ status: 'ready', key })
        return
      }
      try {
        // A pasted session link cannot attach an arbitrary conversation to a paper.
        if (!/^[\w-]{1,100}$/.test(discussionId)) throw new Error('Invalid discussion')
        if (reviewSession && discussionId !== reviewSession) throw new Error('Mismatched review session')
        if (reviewId || !knownIds.current.has(discussionId) || !showCachedSession(discussionId)) {
          const saved = await getSession(discussionId, controller.signal)
          if (controller.signal.aborted) return
          if (reviewId) {
            if ((saved.preferences?.workspace_id || '') !== workspace || !discussionUsesReview(saved.messages,
              { kind: 'edition', id: editionId, revision, scope, locator: { blob_hash: originalHash } },
              { kind: 'paper_review', id: reviewId, revision: reviewRevision!, scope: reviewScope! },
            )) throw new Error('Mismatched review discussion')
          } else if (!discussionUsesEdition(saved.messages, { kind: 'edition', id: editionId, revision, scope })) {
            throw new Error('Mismatched discussion')
          }
          knownIds.current.add(discussionId)
          if (!showCachedSession(discussionId)) await loadSession(discussionId, { signal: controller.signal })
        }
        if (!controller.signal.aborted) setBinding({ status: 'ready', key: discussionId, sessionId: discussionId })
      } catch {
        if (!controller.signal.aborted) setBinding({ status: 'error' })
      }
    })
    return () => controller.abort()
  }, [active, editionId, revision, scope, originalHash, reviewId, reviewRevision, reviewScope, reviewSession, loadSession, newSession, showCachedSession, target, workspace])

  const ownsSession = binding.status === 'ready' && (
    state.sessionKey === binding.key || state.boundFromKey === binding.key ||
    (!!binding.sessionId && state.sessionId === binding.sessionId)
  ) && (state.workspaceId || '') === workspace
  useLayoutEffect(() => { submissionBinding.current = { binding, owns: active && ownsSession } }, [active, binding, ownsSession])
  const boundId = ownsSession ? state.sessionId : null
  const title = ownsSession ? state.sessionTitle : ''
  useEffect(() => {
    if (!boundId) return
    lastDiscussionId.current = boundId
    knownIds.current.add(boundId)
    onSessionBound(boundId)
    if (!historyKey) return
    // Save just the server session identity and display title. The transcript
    // and its sources remain in DeepTutor's scoped session storage.
    const next = [{ id: boundId, title }, ...readDiscussions(historyKey).filter(row => row.id !== boundId)].slice(0, maxDiscussions)
    browserStorage.writeRaw('local', historyKey, JSON.stringify(next))
    const timer = window.setTimeout(() => setHistory(next), 0)
    return () => window.clearTimeout(timer)
  }, [boundId, historyKey, onSessionBound, title])

  useEffect(() => {
    if (!prompt || appliedPrompt.current === prompt.nonce) return
    const timer = window.setTimeout(() => {
      appliedPrompt.current = prompt.nonce
      setDraft(current => current.trim() ? current : prompt.text)
      inputRef.current?.focus({ preventScroll: true })
    }, 0)
    return () => window.clearTimeout(timer)
  }, [prompt])

  const messages = ownsSession ? state.messages : []
  const streaming = ownsSession && state.isStreaming
  const awaiting = streaming && hasPendingAskUser(messages.at(-1)?.events)
  const visible = buildVisiblePath(messages, state.selectedBranches).messages
  const atChronologicalTip = visible.at(-1)?.id === messages.at(-1)?.id
  const last = visible.at(-1)
  const groundedRef = retrying?.snapshot?.studioReferences?.find(ref =>
    ref.kind === 'edition' && ref.id === editionId && ref.revision === revision) ?? reference
  const { ref: composerRef, height: composerHeight } = useMeasuredHeight<HTMLFormElement>()
  const { containerRef, endRef, shouldAutoScrollRef, handleScroll } = useChatAutoScroll({
    hasMessages: messages.length > 0, isStreaming: streaming, composerHeight,
    messageCount: messages.length, lastMessageContent: last?.content, lastEventCount: last?.events?.length,
  })

  function selectDiscussion(id: string) {
    lastDiscussionId.current = id
    submissionBinding.current = { binding: { status: 'loading' }, owns: false }
    setBinding({ status: 'loading' })
    setSubmitError(false)
    setCopied(false)
    drafts.current.set(boundId || slot, draft)
    setRetrying(null)
    setDraft(drafts.current.get(id || newDraftSlot) || '')
    setSources([])
    setTarget(previous => ({ id, request: previous.request + 1 }))
    if (!id) onSessionBound('')
  }

  async function submit(textOverride?: string) {
    const submittedDraft = textOverride ?? draft
    const content = submittedDraft.trim()
    if (!active || !ownsSession || !content || (streaming && !awaiting) || submitLock.current) return
    const startedIn = binding
    const stillBound = () => alive.current && submissionBinding.current.owns &&
      submissionBinding.current.binding === startedIn
    submitLock.current = true
    setSubmitError(false)
    try {
      shouldAutoScrollRef.current = true
      // A refused answer must not silently turn into a different message.
      const accepted = awaiting
        ? await submitUserReply({ text: content })
        : await sendMessage(content, undefined, undefined, undefined, undefined, {
            studioReferences: normalizeStudioReferences([
              ...(retrying?.snapshot?.studioReferences ?? [reference, ...(review ? [review.reference] : [])]), ...sources.map(item => item.ref),
            ]),
            ...(review ? { resourceSelection: { skills: ['best-paper-review'], mcp: [] } } : {}),
            ...(retrying ? { parentMessageId: retrying.parentMessageId } : {}),
          })
      const submittedSlot = boundId || slot
      if (accepted && drafts.current.get(submittedSlot) === submittedDraft) drafts.current.delete(submittedSlot)
      if (stillBound()) {
        if (accepted) {
          setDraft(current => current === submittedDraft ? '' : current)
          setRetrying(null)
        } else setSubmitError(true)
      }
    } catch {
      if (stillBound()) setSubmitError(true)
    } finally {
      submitLock.current = false
    }
  }

  const submitLatest = useRef(submit)
  useLayoutEffect(() => { submitLatest.current = submit })
  const attemptedStart = useRef<string | null>(null)
  const hasUserMessage = messages.some(message => message.role === 'user')
  useEffect(() => {
    if (!active || !ownsSession || streaming || !review?.start || attemptedStart.current === review.start.nonce) return
    const start = review.start
    const timer = window.setTimeout(() => {
      attemptedStart.current = start.nonce
      review.onStartAttempted()
      // A restored or already-submitted session is never started a second time.
      if (hasUserMessage) return
      setDraft(start.text)
      void submitLatest.current(start.text)
    }, 0)
    return () => window.clearTimeout(timer)
  }, [active, ownsSession, streaming, review, hasUserMessage])
  const lastCheckpoint = useRef('')
  useEffect(() => {
    if (!review || !ownsSession || streaming || last?.role !== 'assistant') return
    const checkpoint = `${boundId}:${last.id}:${last.content.length}:${last.events?.length ?? 0}`
    if (checkpoint === lastCheckpoint.current) return
    lastCheckpoint.current = checkpoint
    review.onCheckpoint()
  }, [review, ownsSession, streaming, last, boundId])

  // A source link for the open immutable revision moves the reader, not the
  // conversation. Links to other papers still follow their own normal routes.
  function jumpFromLink(event: MouseEvent<HTMLDivElement>) {
    if (review && !review.allowInlineJump) return
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    const target = event.target instanceof Element ? event.target.closest('a[href]') : null
    if (!(target instanceof HTMLAnchorElement)) return
    const url = new URL(target.href)
    const page = Number(url.searchParams.get('page'))
    if (url.origin !== window.location.origin ||
      url.pathname !== `/library/${encodeURIComponent(editionId)}` ||
      url.searchParams.get('revision') !== String(revision) ||
      (url.searchParams.has('dt_workspace') && url.searchParams.get('dt_workspace') !== workspace) ||
      !Number.isSafeInteger(page) || page < 1) return
    event.preventDefault()
    onJump(page, url.searchParams.get('file') || undefined)
  }

  const onCopy = useCallback(async (text: string) => { await copyText(text) }, [])
  return (
    <section className="flex h-full min-h-0 min-w-0 flex-col" aria-label={t(review ? '审稿对话' : 'Paper tutor')}>
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border px-3 py-2">
        <strong className="flex min-w-0 items-center gap-2 text-sm"><MessageCircle size={15} aria-hidden />{t(review ? '审稿对话' : 'Paper tutor')}</strong>
        <div className="flex min-w-0 items-center gap-1">
          {!review && !!history.length && (
            <select className="max-w-36 truncate rounded-md border border-border bg-background p-1 text-xs"
              value={boundId || target.id} disabled={awaiting} onChange={event => selectDiscussion(event.target.value)} aria-label={t('Saved paper discussions')}>
              {!boundId && !target.id && <option value="">{t('New discussion')}</option>}
              {target.id && !history.some(row => row.id === target.id) && <option value={target.id}>{t('Paper tutor')}</option>}
              {history.map(row => <option key={row.id} value={row.id}>{row.title || t('New discussion')}</option>)}
            </select>
          )}
          {!review && boundId && (
            <Button variant="ghost" size="sm" aria-label={t('Copy discussion link')} title={t('Copy discussion link')}
              onClick={async () => {
                const url = new URL(scopedUrl(studioReferenceHref(reference), workspace), window.location.origin)
                url.searchParams.set('session', boundId)
                try { await copyText(url.toString()); setCopied(true) }
                catch { setCopied(false) }
              }}><Link2 size={15} /></Button>
          )}
          {!review && <Button variant="ghost" size="sm" aria-label={t('New discussion')} title={t('New discussion')}
            disabled={binding.status === 'loading' || streaming} onClick={() => selectDiscussion('')}><Plus size={16} /></Button>}
        </div>
      </div>
      {copied && <p role="status" className="px-3 pt-2 text-xs text-muted-foreground">{t('Link copied')}</p>}
      <div ref={containerRef} onScroll={handleScroll} onClick={jumpFromLink} className="min-h-0 flex-1 overflow-y-auto p-3">
        {binding.status === 'loading' && <p role="status" className="text-sm text-muted-foreground">{t('Loading...')}</p>}
        {binding.status === 'error' && (
          <InlineAlert tone="danger">
            {t(review ? '审稿对话无法打开，原稿版本或会话归属需要重新核对。' : 'This paper discussion could not be opened.')}
            <button type="button" className="ml-2 underline" onClick={() => selectDiscussion(target.id)}>{t('Retry')}</button>
          </InlineAlert>
        )}
        {binding.status === 'ready' && !messages.length && (
          <p className="text-sm text-muted-foreground">{t(review ? '这段对话使用本次审稿固定的原稿和审稿模式。' : 'Ask about the current page or select a passage in the paper.')}</p>
        )}
        {!!messages.length && (
          <ChatMessageList messages={messages} isStreaming={streaming} sessionId={boundId} language={state.language}
            onCopyAssistantMessage={onCopy}
            onRegenerateMessage={() => { if (atChronologicalTip) regenerateLastMessage() }}
            onRetryTurn={message => {
              if (draft.trim()) return
              setRetrying({ parentMessageId: message.parentMessageId ?? null, snapshot: message.requestSnapshot })
              setDraft(message.content)
              inputRef.current?.focus()
            }}
            showModeBadge={false} compactActions
            onSubmitUserReply={submitUserReply} canResendLastTurn={state.lastTurnFailed && atChronologicalTip}
            onResendLastTurn={atChronologicalTip ? resendLastMessage : undefined}
            onSwitchBranch={switchBranch} selectedBranches={state.selectedBranches}
            onRetryFailedSubmission={retryFailedSubmission} onDiscardFailedSubmission={discardFailedSubmission} />
        )}
        <div ref={endRef} className="h-px" />
      </div>
      <form ref={composerRef} className="shrink-0 space-y-2 border-t border-border p-3"
        onSubmit={event => { event.preventDefault(); void submit() }}>
        {retrying && <div role="status" className="flex items-center justify-between rounded-md bg-muted p-1 text-xs">
          {t('Retrying this turn')}
          <Button size="sm" variant="ghost" type="button" onClick={() => setRetrying(null)}>{t('Cancel retry')}</Button>
        </div>}
        <div className="flex min-w-0 flex-wrap items-center gap-1.5 text-xs">
          <Link href={scopedUrl(studioReferenceHref(groundedRef), workspace)}
            onClick={event => {
              if (review && !review.allowInlineJump) return
              if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
              event.preventDefault()
              onJump(groundedRef.locator?.page || 1, groundedRef.locator?.blob_hash)
            }}
            className="flex min-w-0 items-center gap-1 rounded-md bg-muted px-2 py-1 text-foreground">
            <BookOpen size={13} aria-hidden className="shrink-0" />
            <span className="max-w-44 truncate">{paperTitle}</span>
            {groundedRef.locator?.page && <span className="shrink-0"> · {t('Page {{number}}', { number: groundedRef.locator.page })}</span>}
          </Link>
          {!review && !retrying && reference.locator?.quote && <button type="button" aria-label={t('Clear selection')} onClick={onClearSelection} className="rounded-md p-1 hover:bg-muted"><X size={14} /></button>}
          {sources.map(source => (
            <button key={studioReferenceKey(source.ref)} type="button" className="flex min-w-0 items-center gap-1 rounded-md bg-muted px-2 py-1"
              aria-label={t('Remove source: {{title}}', { title: source.title })}
              onClick={() => setSources(previous => previous.filter(row => studioReferenceKey(row.ref) !== studioReferenceKey(source.ref)))}>
              <span className="max-w-32 truncate">{source.title}</span><X size={12} aria-hidden />
            </button>
          ))}
        </div>
        {groundedRef.locator?.quote && <p className="line-clamp-2 border-l-2 border-border pl-2 text-xs text-muted-foreground" title={groundedRef.locator.quote}>{groundedRef.locator.quote}</p>}
        {submitError && <InlineAlert tone="danger">{t('Could not send this message. Please try again.')}</InlineAlert>}
        <textarea ref={inputRef} aria-label={t(review ? '继续审稿或讨论修改' : 'Ask about this paper')} placeholder={t(review ? '继续审稿或讨论修改' : 'Ask about this paper')} rows={2}
          className="block w-full resize-y rounded-xl border border-border bg-background p-2 text-sm outline-none focus:ring-2 focus:ring-ring"
          value={draft} onChange={event => setDraft(event.target.value)} disabled={!ownsSession}
          onKeyDown={event => {
            if (shouldSubmitOnEnter(event)) {
              event.preventDefault()
              void submit()
            }
          }} />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap gap-1">
            <Button type="button" variant="ghost" size="sm" onClick={() => setPicker(true)} disabled={!ownsSession}>{t('Research sources')}</Button>
            {!review && <Button type="button" variant="ghost" size="sm" disabled={!ownsSession || streaming}
              onClick={() => {
                setDraft(current => current.trim() ? current : t('Give me one hint about this passage without showing the full answer.'))
                inputRef.current?.focus()
              }}>{t('Hint')}</Button>}
            {onPractice && <Button type="button" variant="ghost" size="sm" loading={practiceBusy}
              disabled={streaming} onClick={onPractice}>{t('Practice')}</Button>}
          </div>
          {streaming && !awaiting
            ? <Button type="button" variant="secondary" size="sm" onClick={cancelStreamingTurn}>{t('Stop')}</Button>
            : <Button type="submit" size="sm" disabled={!ownsSession || !draft.trim()}>{t('Send')}</Button>}
        </div>
      </form>
      {picker && (
        <StudioReferencePicker workspace={workspace} initial={sources} limit={STUDIO_SOURCE_LIMIT - (review ? 2 : 1)}
          onClose={() => setPicker(false)} onApply={rows => { setSources(rows); setPicker(false) }} />
      )}
    </section>
  )
}
