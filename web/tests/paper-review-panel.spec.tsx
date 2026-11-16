import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import PaperReviewPanel from '@/components/studio/PaperReviewPanel'
import type { PaperReview } from '@/lib/paper-review-api'
import type { StudioSourceReference } from '@/lib/studio-references'
import type { PaperReviewConversation } from '@/components/studio/StudioTutorCompanion'

const api = vi.hoisted(() => ({
  list: vi.fn(), get: vi.fn(), create: vi.fn(), ensure: vi.fn(), update: vi.fn(), options: vi.fn(),
  principal: 'user:a', conversation: vi.fn(),
  t: (key: string, values?: Record<string, unknown>) => key.replace(/{{(\w+)}}/g, (_, name) => String(values?.[name] ?? '')),
}))
vi.mock('react-i18next', () => ({ useTranslation: () => ({
  i18n: { resolvedLanguage: 'zh', language: 'zh' },
  t: api.t,
}) }))
vi.mock('@/hooks/useRecoveryPrincipal', () => ({ useRecoveryPrincipal: () => api.principal }))
vi.mock('@/lib/recovery-principal', () => ({ recoveryPrincipal: () => api.principal }))
vi.mock('@/lib/workspace-scope', () => ({ activeWorkspaceId: () => 'project', scopedUrl: (url: string) => `${url}&dt_workspace=project` }))
vi.mock('@/lib/paper-review-api', async original => ({
  ...await original<typeof import('@/lib/paper-review-api')>(),
  listPaperReviews: api.list, getPaperReview: api.get, createPaperReview: api.create,
  ensurePaperReviewSession: api.ensure, updatePaperReviewIssue: api.update, paperReviewOptions: api.options,
}))
vi.mock('@/components/studio/StudioTutorCompanion', () => ({ default: (props: { review: PaperReviewConversation }) => {
  api.conversation(props)
  return <div data-testid="native-review">{props.review.sessionId}</div>
} }))

const original: StudioSourceReference = { scope: '@library', kind: 'edition', id: 'paper', revision: 4, locator: { blob_hash: 'a'.repeat(64) } }
const props = { workspace: 'project', target: { kind: 'edition' as const, reference: original } }
function record(revision = 1): PaperReview {
  return {
    id: 'review', kind: 'paper_review', scope: 'project', revision, created: 10, updated: 10,
    review_ref: { id: 'review', kind: 'paper_review', scope: 'project', revision: 1 },
    data: { title: 'My paper', mode: 'self', venue: 'general', language: 'zh', instructions: '',
      skill: { name: 'best-paper-review', version: '1', commit: 'commit', tree_hash: 'hash' }, session_id: 'bound-review',
      target_ref: original, checkpoint_hash: 'checkpoint', status: 'checkpointed',
      checks: { quotes: 'needs_review', upstream: 'incomplete', scientific_claims: 'not_automatically_verified' },
      pending: ['Scientific judgments require author review.', 'Original unlocalized diagnostic'],
      issues: [{ id: 'issue-1', title: 'Clarify evaluation', body: 'The sample size is unclear.', request: 'State the sample size.',
        severity: 'major', status: 'question', confidence: 'high', page: 1, quote: 'We evaluate our method.',
        location_status: 'matched', tracking_status: 'open', response: '',
      }, { id: 'issue-2', title: 'Check baseline', body: 'A baseline may be missing.', request: '', severity: 'minor', status: 'question',
        confidence: 'low', page: 2, quote: '', location_status: 'unverified', tracking_status: 'open', response: '',
      }],
    },
  }
}

beforeEach(() => {
  api.principal = 'user:a'
  api.list.mockReset().mockResolvedValue({ reviews: [record()] })
  api.get.mockReset().mockResolvedValue(record())
  api.create.mockReset().mockResolvedValue({ ...record(), data: { ...record().data, session_id: '', status: 'draft' } })
  api.ensure.mockReset().mockResolvedValue(record(2))
  api.update.mockReset()
  api.options.mockReset().mockResolvedValue({ venues: [{ id: 'general', label: 'General' }, { id: 'CVPR', label: 'Computer Vision and Pattern Recognition' }], modes: [] })
  api.conversation.mockReset()
})

it('keeps unfinished checks collapsed and only links verified original locations', async () => {
  const onJump = vi.fn()
  render(<PaperReviewPanel {...props} onJump={onJump} />)
  await screen.findByText('审稿草稿')
  const pending = screen.getByText('待完成检查（2）').closest('details')!
  expect(pending).not.toHaveAttribute('open')
  fireEvent.click(screen.getByText('待完成检查（2）'))
  expect(screen.getByText('科学判断仍需作者复核。')).toBeVisible()
  expect(screen.getByText('Original unlocalized diagnostic')).not.toBeVisible()
  fireEvent.click(screen.getByText('查看检查记录'))
  expect(screen.getByText('仍需作者复核')).toBeVisible()
  expect(screen.queryByText('not_automatically_verified')).not.toBeInTheDocument()
  fireEvent.click(screen.getByText('Clarify evaluation'))
  fireEvent.click(screen.getByRole('link', { name: '第 1 页' }))
  expect(onJump).toHaveBeenCalledWith(original, 1, 'We evaluate our method.')
  fireEvent.click(screen.getByText('Check baseline'))
  expect(screen.getByText('原文位置待核对')).toBeVisible()
  expect(screen.queryByRole('link', { name: '第 2 页' })).not.toBeInTheDocument()
  expect(screen.queryByText('high')).not.toBeInTheDocument()
})

it('binds a real native session before starting and restores it from the review API', async () => {
  api.list.mockResolvedValue({ reviews: [] })
  let complete!: (review: PaperReview) => void
  api.ensure.mockImplementationOnce(() => new Promise(resolve => { complete = resolve }))
  const view = render(<PaperReviewPanel {...props} />)
  await screen.findByText('还没有审稿记录。审稿会绑定当前原稿版本。')
  fireEvent.click(screen.getByRole('button', { name: /新审稿/ }))
  await screen.findByRole('option', { name: 'CVPR' })
  expect(screen.getByRole('option', { name: 'CVPR' })).toHaveAttribute('title', 'Computer Vision and Pattern Recognition')
  fireEvent.change(screen.getByLabelText('审稿模式'), { target: { value: 'revise' } })
  fireEvent.change(screen.getByLabelText('目标会议或期刊'), { target: { value: 'CVPR' } })
  fireEvent.change(screen.getByLabelText('补充要求'), { target: { value: 'Check revisions' } })
  fireEvent.click(screen.getByRole('button', { name: '开始审稿' }))
  await waitFor(() => expect(api.ensure).toHaveBeenCalledWith('project', 'review'))
  expect(api.create).toHaveBeenCalledWith('project', expect.objectContaining({
    edition_id: 'paper', revision: 4, blob_hash: original.locator!.blob_hash, mode: 'revise', venue: 'CVPR', instructions: 'Check revisions', language: 'zh',
  }))
  expect(api.conversation).not.toHaveBeenCalled()
  await act(async () => { complete(record(2)) })
  expect(await screen.findByTestId('native-review')).toHaveTextContent('bound-review')
  expect(api.conversation.mock.lastCall![0]).toEqual(expect.objectContaining({
    reference: original, review: expect.objectContaining({ reference: record().review_ref, sessionId: 'bound-review', start: expect.any(Object) }),
  }))
  view.unmount()
  api.list.mockResolvedValue({ reviews: [record(2)] })
  render(<PaperReviewPanel {...props} />)
  await screen.findByText('审稿草稿')
  expect(api.conversation.mock.lastCall![0].review).toEqual(expect.objectContaining({ sessionId: 'bound-review', start: null }))
  expect(api.create).toHaveBeenCalledTimes(1)
})

it('keeps newer saved progress over a late GET and clears only recovered network errors', async () => {
  render(<PaperReviewPanel {...props} />)
  await screen.findByText('Clarify evaluation')
  let complete!: (review: PaperReview) => void
  api.get.mockImplementationOnce(() => new Promise(resolve => { complete = resolve }))
  fireEvent.click(screen.getByRole('button', { name: '刷新审稿记录' }))
  fireEvent.click(screen.getByText('Clarify evaluation'))
  const issue = screen.getByText('Clarify evaluation').closest('details')!
  fireEvent.click(within(issue).getByRole('button', { name: '记录处理进度' }))
  fireEvent.change(screen.getByLabelText('处理状态'), { target: { value: 'working' } })
  fireEvent.change(screen.getByLabelText('回应或修改说明'), { target: { value: 'Saved revision note' } })
  const newer = record(2)
  newer.data.issues[0].tracking_status = 'working'
  newer.data.issues[0].response = 'Saved revision note'
  api.update.mockResolvedValueOnce(newer)
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  await waitFor(() => expect(screen.queryByLabelText('回应或修改说明')).not.toBeInTheDocument())
  await act(async () => { complete(record(1)) })
  expect(screen.getByText('Saved revision note')).toBeVisible()
  expect(screen.getByText('修订中')).toBeVisible()

  api.get.mockRejectedValueOnce(new Error('Network unavailable'))
  fireEvent.click(screen.getByRole('button', { name: '刷新审稿记录' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Network unavailable')
  api.get.mockResolvedValue(newer)
  fireEvent.click(screen.getByRole('button', { name: '刷新审稿记录' }))
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())

  fireEvent.click(within(issue).getByRole('button', { name: '记录处理进度' }))
  fireEvent.change(screen.getByLabelText('回应或修改说明'), { target: { value: 'Unsubmitted draft' } })
  api.update.mockRejectedValueOnce(new Error('Revision conflict'))
  fireEvent.click(screen.getByRole('button', { name: '保存' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Revision conflict')
  fireEvent.click(screen.getByRole('button', { name: '刷新审稿记录' }))
  await waitFor(() => expect(api.get).toHaveBeenCalledTimes(4))
  expect(screen.getByRole('alert')).toHaveTextContent('Revision conflict')
  expect(screen.getByLabelText('回应或修改说明')).toHaveValue('Unsubmitted draft')
})

it('does not bind or start an old account’s review after an account switch', async () => {
  api.list.mockResolvedValue({ reviews: [] })
  let complete!: (review: PaperReview) => void
  api.create.mockImplementationOnce(() => new Promise(resolve => { complete = resolve }))
  const view = render(<PaperReviewPanel {...props} />)
  await screen.findByText('还没有审稿记录。审稿会绑定当前原稿版本。')
  fireEvent.click(screen.getByRole('button', { name: /新审稿/ }))
  fireEvent.click(screen.getByRole('button', { name: '开始审稿' }))
  await waitFor(() => expect(api.create).toHaveBeenCalledTimes(1))
  api.principal = 'user:b'
  view.rerender(<PaperReviewPanel {...props} />)
  await act(async () => { complete(record()) })
  expect(api.ensure).not.toHaveBeenCalled()
  expect(api.conversation).not.toHaveBeenCalled()
})

it('opens the requested review only when its frozen original matches the reader', async () => {
  const latest = { ...record(), id: 'latest', created: 20 }
  const foreign = { ...record(), id: 'foreign', data: { ...record().data, target_ref: { ...original, locator: { blob_hash: 'b'.repeat(64) } } } }
  api.list.mockResolvedValue({ reviews: [latest, record(), foreign] })
  const view = render(<PaperReviewPanel {...props} initialReviewId="review" />)
  await screen.findByText('审稿草稿')
  expect(screen.getByRole('combobox', { name: '审稿记录' })).toHaveValue('review')
  view.unmount()
  api.conversation.mockClear()
  render(<PaperReviewPanel {...props} initialReviewId="foreign" />)
  expect(await screen.findByRole('alert')).toHaveTextContent('原稿版本或会话归属需要重新核对')
  expect(screen.getByRole('combobox', { name: '审稿记录' })).toHaveValue('')
  expect(api.conversation).not.toHaveBeenCalled()
})
