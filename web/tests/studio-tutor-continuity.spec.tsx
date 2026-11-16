import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import StudioReferencePicker from '@/components/chat/StudioReferencePicker'
import StudioTutorCompanion, { discussionUsesEdition, discussionUsesReview } from '@/components/studio/StudioTutorCompanion'
import { buildStartTurnInput } from '@/features/chat/controllers/buildStartTurnInput'
import {
  consumeStudioReferences, handoffStudioReferences, studioCitations, studioReferenceHref,
  type StudioSourceItem, type StudioSourceReference,
} from '@/lib/studio-references'

const mocks = vi.hoisted(() => ({
  studio: vi.fn(),
  principal: 'user:local-test' as string | null,
  loadSession: vi.fn(),
  showCachedSession: vi.fn(),
  sendMessage: vi.fn(),
  submitUserReply: vi.fn(),
  newSession: vi.fn(),
  getSession: vi.fn(),
  state: {
    sessionKey: 'unrelated-chat', sessionId: 'unrelated-chat', boundFromKey: null as string | null,
    sessionTitle: '', workspaceId: 'a', isStreaming: false, language: 'en',
    messages: [] as { id: number; role: string; content: string; events?: unknown[]; parentMessageId?: number | null; requestSnapshot?: unknown }[],
    selectedBranches: {} as Record<string, number>,
  },
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (value: string, vars?: Record<string, string>) =>
    value.replace(/{{(\w+)}}/g, (_, key) => String(vars?.[key] ?? '')) }),
}))
vi.mock('@/lib/studio-api', () => ({ studio: mocks.studio }))
vi.mock('@/lib/session-api', () => ({ getSession: mocks.getSession }))
vi.mock('@/lib/recovery-principal', () => ({ recoveryPrincipal: () => mocks.principal, RECOVERY_PRINCIPAL_CHANGED: 'test-principal-change' }))
vi.mock('@/features/chat/ChatStateAdapter', () => ({
  useChatStateAdapter: () => ({
    ...mocks,
    cancelStreamingTurn: vi.fn(), regenerateLastMessage: vi.fn(),
    resendLastMessage: vi.fn(), retryFailedSubmission: vi.fn(),
    discardFailedSubmission: vi.fn(), switchBranch: vi.fn(),
  }),
}))
vi.mock('@/features/chat/messages', () => ({
  ChatMessageList: ({ messages, onRetryTurn }: {
    messages: typeof mocks.state.messages
    onRetryTurn?: (message: typeof mocks.state.messages[number]) => void
  }) =>
    <ol>{messages.map(msg => <li key={msg.content}>{msg.content}
      {msg.role === 'user' && onRetryTurn && <button onClick={() => onRetryTurn(msg)}>Retry {msg.content}</button>}
    </li>)}</ol>,
}))

const hash = 'a'.repeat(64)
const paper: StudioSourceReference = {
  scope: '@library', kind: 'edition', id: 'paper-1', revision: 3, locator: { blob_hash: hash, page: 8 },
}
const experiment: StudioSourceReference = { scope: 'a', kind: 'run', id: 'run-1', revision: 2 }
const snapshot: StudioSourceReference = { scope: 'a', kind: 'remote_snapshot', id: 'snap-1', revision: 1 }
const reviewRef: StudioSourceReference = { scope: 'a', kind: 'paper_review', id: 'review-1', revision: 1 }
const item = (ref: StudioSourceReference, title = ref.id): StudioSourceItem => ({
  ref, title, description: '', href: studioReferenceHref(ref),
})

beforeEach(() => {
  mocks.principal = 'user:local-test'
  mocks.state.selectedBranches = {}
  mocks.studio.mockReset()
  mocks.sendMessage.mockReset().mockResolvedValue(true)
  mocks.submitUserReply.mockReset().mockResolvedValue(true)
  mocks.state.isStreaming = false
  mocks.getSession.mockReset()
  mocks.loadSession.mockReset()
  mocks.showCachedSession.mockReset()
  mocks.newSession.mockReset().mockReturnValue('paper-draft')
  mocks.state.sessionKey = 'unrelated-chat'
  mocks.state.sessionId = 'unrelated-chat'
  mocks.state.boundFromKey = null
  mocks.state.messages = []
})

it('resumes an API-bound review and starts once with the frozen review skill and original', async () => {
  mocks.state.sessionKey = 'review-session'
  mocks.state.sessionId = 'review-session'
  mocks.showCachedSession.mockReturnValue(true)
  let load!: (value: unknown) => void
  mocks.getSession.mockImplementationOnce(() => new Promise(resolve => { load = resolve }))
  const review = { reference: reviewRef, sessionId: 'review-session', mode: 'self', onCheckpoint: vi.fn(),
    start: { nonce: 'request-1', text: 'Review this frozen manuscript' }, onStartAttempted: vi.fn() }
  const props = { workspace: 'a', reference: paper, paperTitle: 'Original paper', initialSessionId: 'review-session',
    prompt: null, onSessionBound: vi.fn(), onJump: vi.fn(), onClearSelection: vi.fn(), practiceBusy: false, review }
  const view = render(<StudioTutorCompanion {...props} />)
  expect(screen.getByRole('textbox', { name: '继续审稿或讨论修改' })).toBeDisabled()
  expect(mocks.sendMessage).not.toHaveBeenCalled()
  await waitFor(() => expect(mocks.getSession).toHaveBeenCalledTimes(1))
  await act(async () => { load({ preferences: { workspace_id: 'a' }, messages: [] }) })
  await waitFor(() => expect(mocks.sendMessage).toHaveBeenCalledTimes(1))
  expect(mocks.sendMessage).toHaveBeenCalledWith('Review this frozen manuscript', undefined, undefined, undefined, undefined, {
    studioReferences: [paper, reviewRef], resourceSelection: { skills: ['best-paper-review'], mcp: [] },
  })
  expect(mocks.newSession).not.toHaveBeenCalled()
  expect(review.onStartAttempted).toHaveBeenCalledTimes(1)
  const opening = { role: 'user', metadata: { request_snapshot: { studioReferences: [paper, reviewRef] } } }
  mocks.getSession.mockResolvedValue({ preferences: { workspace_id: 'a' }, messages: [opening] })
  view.rerender(<StudioTutorCompanion {...props} active={false} />)
  const calls = mocks.getSession.mock.calls.length
  await act(async () => {})
  expect(mocks.getSession).toHaveBeenCalledTimes(calls)
  view.rerender(<StudioTutorCompanion {...props} active />)
  await waitFor(() => expect(mocks.getSession).toHaveBeenCalledTimes(calls + 1))
  expect(mocks.sendMessage).toHaveBeenCalledTimes(1)
})

it('refuses to resume a review session bound to another original or workspace', async () => {
  const opening = { role: 'user', metadata: { request_snapshot: { studioReferences: [paper, reviewRef] } } }
  expect(discussionUsesReview([opening], paper, reviewRef)).toBe(true)
  expect(discussionUsesReview([opening], { ...paper, locator: { blob_hash: 'different' } }, reviewRef)).toBe(false)
  expect(discussionUsesReview([opening], paper, { ...reviewRef, scope: 'other' })).toBe(false)
  mocks.state.sessionKey = 'review-session'
  mocks.state.sessionId = 'review-session'
  mocks.getSession.mockResolvedValue({ preferences: { workspace_id: 'other' }, messages: [opening] })
  render(<StudioTutorCompanion workspace="a" reference={paper} paperTitle="Original paper" initialSessionId="review-session"
    prompt={null} onSessionBound={vi.fn()} onJump={vi.fn()} onClearSelection={vi.fn()} practiceBusy={false}
    review={{ reference: reviewRef, sessionId: 'review-session', mode: 'full', onCheckpoint: vi.fn(), start: null, onStartAttempted: vi.fn() }} />)
  expect(await screen.findByRole('alert')).toHaveTextContent('原稿版本或会话归属需要重新核对')
  expect(screen.getByRole('textbox', { name: '继续审稿或讨论修改' })).toBeDisabled()
  expect(mocks.sendMessage).not.toHaveBeenCalled()
})

it('preserves typed frozen identities through the composer handoff and wire request', () => {
  handoffStudioReferences('a', [item(paper), item(experiment), item(snapshot)])
  expect(consumeStudioReferences('b')).toEqual([])
  const sources = consumeStudioReferences('a')
  expect(sources.map(row => row.ref)).toEqual([paper, experiment, snapshot])
  expect(consumeStudioReferences('a')).toEqual([])
  const command = buildStartTurnInput({ content: 'Compare these records', studioReferences: sources.map(row => row.ref) })
  expect(command.studio_references).toEqual([paper, experiment, snapshot])
  expect(command.config).not.toHaveProperty('studio_references')
  expect(studioReferenceHref(command.studio_references![0] as StudioSourceReference))
    .toBe(`/library/paper-1?revision=3&file=${hash}&page=8`)
})

it('trusts source citations only from scoped app routes and never accepts an arbitrary discussion', () => {
  const meta = { title: 'Original paper', revision: 3, href: studioReferenceHref(paper), page: 8 }
  const events = [
    { metadata: { tool_metadata: { studio_citation: meta } } },
    { metadata: { tool_metadata: { studio_citation: meta } } },
    { metadata: { studio_citation: { ...meta, href: '//evil.test/library/paper-1' } } },
    { metadata: { studio_citation: { ...meta, href: 'javascript:alert(1)' } } },
  ]
  expect(studioCitations(events)).toEqual([meta])
  const messages = [
    { role: 'user', metadata: { request_snapshot: { studioReferences: [paper, experiment] } } },
    { role: 'assistant' },
  ]
  expect(discussionUsesEdition(messages, paper)).toBe(true)
  expect(discussionUsesEdition(messages, { ...paper, revision: 4 })).toBe(false)
  expect(discussionUsesEdition([{ role: 'user' }, ...messages], paper)).toBe(false)
})

it('cancels an in-flight source page when the project changes and never mixes selected rows', async () => {
  let finishPage!: (value: { items: StudioSourceItem[] }) => void
  let cancelledPage: AbortSignal | undefined
  mocks.studio.mockImplementation((workspace: string, url: string, _init: unknown, signal: AbortSignal) => {
    if (url.includes('cursor=')) {
      cancelledPage = signal
      return new Promise(resolve => { finishPage = resolve })
    }
    return Promise.resolve(workspace === 'a'
      ? { items: [item(experiment, 'Project A experiment')], next_cursor: 'a-cursor' }
      : { items: [item({ ...snapshot, scope: 'b' }, 'Project B snapshot')] })
  })
  const applied = vi.fn()
  const view = render(<StudioReferencePicker workspace="a" initial={[]} onApply={applied} onClose={() => {}} />)
  fireEvent.click(await screen.findByRole('checkbox', { name: /Project A experiment/ }))
  fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
  view.rerender(<StudioReferencePicker workspace="b" initial={[]} onApply={applied} onClose={() => {}} />)
  await screen.findByRole('checkbox', { name: /Project B snapshot/ })
  expect(cancelledPage?.aborted).toBe(true)
  await act(async () => { finishPage({ items: [item(paper, 'Late A paper')] }) })
  expect(screen.queryByText('Late A paper')).not.toBeInTheDocument()
  expect(screen.queryByText('Project A experiment')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('checkbox', { name: /Project B snapshot/ }))
  fireEvent.click(screen.getByRole('button', { name: 'Use sources' }))
  expect(applied).toHaveBeenCalledWith([expect.objectContaining({ ref: { ...snapshot, scope: 'b' } })])
})

it('sends selected page and follow-up through a dedicated native chat session, never into the prior chat', async () => {
  const onBound = vi.fn()
  const props = {
    workspace: 'a', reference: paper, paperTitle: 'Original paper', initialSessionId: '',
    prompt: null, onSessionBound: onBound, onJump: vi.fn(), onClearSelection: vi.fn(), practiceBusy: false,
  }
  const view = render(<StudioTutorCompanion {...props} />)
  await waitFor(() => expect(mocks.newSession).toHaveBeenCalledWith(expect.objectContaining({
    workspaceId: 'a', capability: 'chat', draftRecoveryScope: expect.stringContaining('paper-1'),
  })))
  const input = screen.getByRole('textbox', { name: 'Ask about this paper' })
  expect(input).toBeDisabled()
  mocks.state.sessionKey = 'paper-draft'
  mocks.state.sessionId = ''
  view.rerender(<StudioTutorCompanion {...props} />)
  await waitFor(() => expect(input).toBeEnabled())
  fireEvent.change(input, { target: { value: 'Explain this page' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  expect(mocks.sendMessage).toHaveBeenLastCalledWith('Explain this page', undefined, undefined, undefined, undefined, {
    studioReferences: [paper],
  })
  mocks.state.sessionKey = 'saved-paper-chat'
  mocks.state.sessionId = 'saved-paper-chat'
  mocks.state.boundFromKey = 'paper-draft'
  mocks.state.messages = [{ id: 1, role: 'assistant', content: 'Saved answer' }]
  const nextPage = { ...paper, locator: { ...paper.locator, page: 9, quote: 'Second passage' } }
  view.rerender(<StudioTutorCompanion {...props} reference={nextPage} />)
  expect(await screen.findByText('Saved answer')).toBeInTheDocument()
  expect(onBound).toHaveBeenCalledWith('saved-paper-chat')
  fireEvent.change(input, { target: { value: 'Explain the next page' } })
  fireEvent.keyDown(input, { key: 'Enter', keyCode: 13 })
  await waitFor(() => expect(mocks.sendMessage).toHaveBeenLastCalledWith('Explain the next page', undefined, undefined, undefined, undefined, {
    studioReferences: [nextPage],
  }))
  // Visiting another tool and returning must select the saved discussion,
  // not create a new draft just because this component began without an ID.
  mocks.showCachedSession.mockReturnValue(true)
  view.rerender(<StudioTutorCompanion {...props} reference={nextPage} active={false} />)
  mocks.state.sessionKey = 'another-tool-session'
  mocks.state.sessionId = 'another-tool-session'
  mocks.state.boundFromKey = null
  view.rerender(<StudioTutorCompanion {...props} reference={nextPage} active />)
  await waitFor(() => expect(mocks.showCachedSession).toHaveBeenCalledWith('saved-paper-chat'))
  expect(mocks.newSession).toHaveBeenCalledTimes(1)
})

it('preserves a draft rejected by the sender and does not erase edits made while a request resolves', async () => {
  const props = {
    workspace: 'a', reference: paper, paperTitle: 'Original paper', initialSessionId: '',
    prompt: null, onSessionBound: vi.fn(), onJump: vi.fn(), onClearSelection: vi.fn(), practiceBusy: false,
  }
  mocks.state.sessionKey = 'paper-draft'
  mocks.state.sessionId = ''
  render(<StudioTutorCompanion {...props} />)
  const input = screen.getByRole('textbox', { name: 'Ask about this paper' })
  await waitFor(() => expect(input).toBeEnabled())
  mocks.sendMessage.mockResolvedValueOnce(false)
  fireEvent.change(input, { target: { value: 'Keep this draft' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not send this message.')
  expect(input).toHaveValue('Keep this draft')
  let complete!: (accepted: boolean) => void
  mocks.sendMessage.mockImplementationOnce(() => new Promise(resolve => { complete = resolve }))
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  fireEvent.change(input, { target: { value: 'An updated draft' } })
  await act(async () => { complete(true) })
  expect(input).toHaveValue('An updated draft')
})

it('treats a paused tutor reply as reply-only, and ignores completion from a previous discussion', async () => {
  const props = {
    workspace: 'a', reference: paper, paperTitle: 'Original paper', initialSessionId: '',
    prompt: null, onSessionBound: vi.fn(), onJump: vi.fn(), onClearSelection: vi.fn(), practiceBusy: false,
  }
  mocks.state.sessionKey = 'paper-draft'
  mocks.state.sessionId = ''
  mocks.state.isStreaming = true
  mocks.state.messages = [{ id: 1, role: 'assistant', content: 'A question', events: [
    { type: 'tool_result', metadata: { ask_user: { question: 'A question' } } },
  ] }]
  mocks.submitUserReply.mockResolvedValueOnce(false)
  const view = render(<StudioTutorCompanion {...props} />)
  const input = screen.getByRole('textbox', { name: 'Ask about this paper' })
  await waitFor(() => expect(input).toBeEnabled())
  fireEvent.change(input, { target: { value: 'My reply' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not send this message.')
  expect(input).toHaveValue('My reply')
  expect(mocks.sendMessage).not.toHaveBeenCalled()

  mocks.state.isStreaming = false
  mocks.state.messages = []
  view.rerender(<StudioTutorCompanion {...props} />)
  let complete!: (accepted: boolean) => void
  mocks.sendMessage.mockImplementationOnce(() => new Promise(resolve => { complete = resolve }))
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  mocks.newSession.mockReturnValueOnce('new-paper-draft')
  fireEvent.click(screen.getByRole('button', { name: 'New discussion' }))
  mocks.state.sessionKey = 'new-paper-draft'
  view.rerender(<StudioTutorCompanion {...props} />)
  await waitFor(() => expect(input).toBeEnabled())
  fireEvent.change(input, { target: { value: 'My new discussion draft' } })
  await act(async () => { complete(false) })
  expect(input).toHaveValue('My new discussion draft')
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

it('retries a selected older branch with its original page and parent instead of the chronological last turn', async () => {
  const currentPage: StudioSourceReference = { ...paper, locator: { ...paper.locator, page: 20 } }
  const newerSource: StudioSourceReference = { ...paper, locator: { ...paper.locator, page: 18 } }
  const props = {
    workspace: 'a', reference: currentPage, paperTitle: 'Original paper', initialSessionId: '',
    prompt: null, onSessionBound: vi.fn(), onJump: vi.fn(), onClearSelection: vi.fn(), practiceBusy: false,
  }
  mocks.state.sessionKey = 'paper-draft'
  mocks.state.sessionId = ''
  mocks.state.messages = [
    { id: 10, role: 'user', content: 'Opening question' },
    { id: 11, role: 'assistant', content: 'Opening response', parentMessageId: 10 },
    { id: 12, role: 'user', content: 'Older question', parentMessageId: 11,
      requestSnapshot: { studioReferences: [paper, experiment] } },
    { id: 13, role: 'assistant', content: 'Older response', parentMessageId: 12 },
    { id: 14, role: 'user', content: 'Later question', parentMessageId: 11,
      requestSnapshot: { studioReferences: [newerSource] } },
    { id: 15, role: 'assistant', content: 'Later response', parentMessageId: 14 },
  ]
  mocks.state.selectedBranches = { '11': 12 }
  render(<StudioTutorCompanion {...props} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Retry Older question' }))
  expect(screen.getByRole('textbox', { name: 'Ask about this paper' })).toHaveValue('Older question')
  expect(screen.getByRole('link', { name: /Original paper.*Page 8/ })).toHaveAttribute(
    'href', `/library/paper-1?revision=3&file=${hash}&page=8&dt_workspace=a`,
  )
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  await waitFor(() => expect(mocks.sendMessage).toHaveBeenCalledWith(
    'Older question', undefined, undefined, undefined, undefined, { parentMessageId: 11, studioReferences: [paper, experiment] },
  ))
})

it('unmounts source results when ownership becomes unresolved and partitions link handoffs by account', async () => {
  mocks.studio.mockResolvedValue({ items: [item(experiment, 'Old account experiment')] })
  const view = render(<StudioReferencePicker workspace="a" initial={[]} onApply={() => {}} onClose={() => {}} />)
  expect(await screen.findByText('Old account experiment')).toBeInTheDocument()
  handoffStudioReferences('a', [item(experiment)])
  await act(async () => {
    mocks.principal = null
    window.dispatchEvent(new Event('test-principal-change'))
  })
  expect(view.queryByRole('dialog')).not.toBeInTheDocument()
  mocks.studio.mockResolvedValue({ items: [item(snapshot, 'New account snapshot')] })
  await act(async () => {
    mocks.principal = 'user:new-test'
    window.dispatchEvent(new Event('test-principal-change'))
  })
  expect(await screen.findByText('New account snapshot')).toBeInTheDocument()
  expect(screen.queryByText('Old account experiment')).not.toBeInTheDocument()
  expect(consumeStudioReferences('a')).toEqual([])
})
