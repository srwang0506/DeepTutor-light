import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import PersonalStudyHome from '@/components/study/PersonalStudyHome'
import { EMPTY_STUDY_SOURCES, type StudySnapshot } from '@/lib/personal-study'

const mocks = vi.hoisted(() => ({ load: vi.fn(), save: vi.fn(), workspace: 'a' }))
vi.mock('@/lib/personal-study-api', async (original) => ({
  ...(await original<object>()),
  loadStudySnapshot: mocks.load,
  updateStudyPreferences: mocks.save,
}))
vi.mock('@/hooks/useActiveWorkspaceId', () => ({ useActiveWorkspaceId: () => mocks.workspace }))
vi.mock('@/components/workspaces/useWorkspaceChoices', () => ({
  useWorkspaceChoices: () => ({ currentName: mocks.workspace }),
}))
vi.mock('@/hooks/useAuthStatus', () => ({
  useAuthStatus: () => ({ loading: false, learningPolicy: null }),
}))
vi.mock('@/lib/session-events', () => ({ subscribeSessionChanges: () => () => {} }))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (text: string) => text === 'Study today' ? '今日学习' : text, i18n: { language: 'zh' } }),
}))

const snapshot = (title = '原来的问题'): StudySnapshot => ({
  sources: EMPTY_STUDY_SOURCES,
  queue: [],
  summary: null,
  failed: [],
  sessions: [
    {
      session_id: 's1',
      id: 's1',
      title,
      content_workspace_id: mocks.workspace,
      message_count: 2,
      last_message: '',
      created_at: 1,
      updated_at: 2,
    },
  ],
  preferences: {
    minutes: 30,
    revision: 4,
    deferred: {},
    now: 1000,
    day_end: 2000,
    timezone: 'UTC',
  },
})
beforeEach(() => {
  vi.clearAllMocks()
  mocks.workspace = 'a'
  mocks.load.mockResolvedValue(snapshot())
})
afterEach(cleanup)

it('saves a time budget in the original project with revision checking', async () => {
  mocks.save.mockResolvedValue({ ...snapshot().preferences, minutes: 15, revision: 5 })
  render(<PersonalStudyHome />)
  expect(screen.getByRole('heading', { level: 1, name: '今日学习' })).toBeVisible()
  await screen.findByRole('link', { name: '接着学' })
  fireEvent.click(screen.getByRole('button', { name: '15 分钟' }))
  await waitFor(() =>
    expect(screen.getByRole('button', { name: '15 分钟' })).toHaveAttribute('aria-pressed', 'true'),
  )
  expect(mocks.save).toHaveBeenCalledWith('a', expect.any(String), {
    minutes: 15,
    expected_revision: 4,
  })
  expect(screen.getByRole('link', { name: '接着学' })).toHaveAttribute(
    'href',
    '/chat/s1?dt_workspace=a',
  )
})

it('keeps the plan visible on a failed deferral, then permits a retry and restoration', async () => {
  mocks.save.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({
    ...snapshot().preferences,
    deferred: { 'chat:s1': 2000 },
    revision: 5,
  })
  render(<PersonalStudyHome />)
  fireEvent.click(await screen.findByRole('button', { name: '明天再学' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('安排没有保存成功')
  expect(screen.getByRole('link', { name: '接着学' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '明天再学' }))
  expect(await screen.findByRole('button', { name: '放回今天' })).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: '接着学' })).not.toBeInTheDocument()
  mocks.save.mockResolvedValueOnce({ ...snapshot().preferences, revision: 6 })
  fireEvent.click(screen.getByRole('button', { name: '放回今天' }))
  expect(await screen.findByRole('link', { name: '接着学' })).toBeInTheDocument()
  expect(mocks.save).toHaveBeenLastCalledWith('a', expect.any(String), {
    expected_revision: 5,
    deferral: { key: 'chat:s1', action: 'restore' },
  })
})

it('never displays a late response from the previous project', async () => {
  const old = snapshot('OLD PROJECT')
  let resolve!: (value: StudySnapshot) => void
  mocks.load.mockReturnValueOnce(
    new Promise<StudySnapshot>((done) => {
      resolve = done
    }),
  )
  const view = render(<PersonalStudyHome />)
  mocks.workspace = 'b'
  mocks.load.mockResolvedValue(snapshot('NEW PROJECT'))
  view.rerender(<PersonalStudyHome />)
  await screen.findByRole('heading', { name: 'NEW PROJECT' })
  await act(async () => {
    resolve(old)
  })
  expect(screen.queryByText('OLD PROJECT')).not.toBeInTheDocument()
  expect(screen.getByRole('link', { name: '接着学' })).toHaveAttribute(
    'href',
    '/chat/s1?dt_workspace=b',
  )
})

it('shows unavailable evidence as unknown and disables unsavable preferences', async () => {
  mocks.load.mockResolvedValue({
    ...snapshot(),
    preferences: null,
    failed: ['preferences', 'practice'],
  })
  render(<PersonalStudyHome />)
  expect(await screen.findByRole('alert')).toHaveTextContent('部分记录暂时无法读取')
  expect(screen.getByRole('button', { name: '15 分钟' })).toBeDisabled()
  expect(screen.getByText('—')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '接着学' })).toBeInTheDocument()
})
