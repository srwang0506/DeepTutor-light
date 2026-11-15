import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import ExperimentsPage from '@/components/studio/ExperimentsPage'

const context = vi.hoisted(() => ({ workspace: 'research-a', principal: 'user-a' }))
const api = vi.hoisted(() => ({ records: vi.fn(), post: vi.fn(), studio: vi.fn() }))
vi.mock('@/hooks/useActiveWorkspaceId', () => ({ useActiveWorkspaceId: () => context.workspace }))
vi.mock('@/hooks/useRecoveryPrincipal', () => ({ useRecoveryPrincipal: () => context.principal }))
vi.mock('@/lib/studio-api', () => api)
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))
vi.mock('@/components/studio/IntegrationSources', () => ({ default: () => null }))
vi.mock('@/components/studio/LiveRunTimeline', () => ({ default: () => null }))

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(accept => { resolve = accept })
  return { promise, resolve }
}

function run(title: string) {
  return {
    id: title,
    kind: 'run',
    scope: context.workspace,
    revision: 1,
    data: { title, status: 'planned', dataset: 'HICO-DET', split: 'test', commit: 'abc', missing: [] },
  }
}

beforeEach(() => {
  context.workspace = 'research-a'
  context.principal = 'user-a'
  api.records.mockReset().mockResolvedValue([])
})

it('waits for a successful response before showing the first-experiment empty state', async () => {
  const request = deferred<ReturnType<typeof run>[]>()
  api.records.mockReturnValue(request.promise)
  render(<ExperimentsPage />)
  expect(screen.getByRole('status')).toHaveTextContent('正在加载实验记录…')
  expect(screen.queryByText('记录真实的实验过程')).not.toBeInTheDocument()

  await act(async () => request.resolve([]))
  expect(screen.getByText('记录真实的实验过程')).toBeVisible()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})

it('offers a retry for load failure instead of claiming the project has no experiments', async () => {
  api.records.mockRejectedValueOnce(new Error('Connection unavailable'))
    .mockResolvedValueOnce([run('Recovered run')])
  render(<ExperimentsPage />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Connection unavailable')
  expect(screen.queryByText('记录真实的实验过程')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  expect(await screen.findByRole('button', { name: 'Recovered run' })).toBeVisible()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

it('explains an empty filter and offers a way back to the existing records', async () => {
  api.records.mockResolvedValue([run('Planned run')])
  render(<ExperimentsPage />)
  await screen.findByRole('button', { name: 'Planned run' })
  fireEvent.click(screen.getByRole('button', { name: '已成功' }))
  expect(screen.getByText('没有符合筛选条件的实验记录')).toBeVisible()
  expect(screen.queryByText('记录真实的实验过程')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '显示全部记录' }))
  expect(screen.getByRole('button', { name: 'Planned run' })).toBeVisible()
  expect(screen.getByRole('button', { name: '全部' })).toHaveAttribute('aria-pressed', 'true')
})

it.each(['workspace', 'principal'] as const)('ignores the old response after a %s switch', async field => {
  const old = deferred<ReturnType<typeof run>[]>()
  api.records.mockReturnValueOnce(old.promise).mockResolvedValue([run('Current project run')])
  const view = render(<ExperimentsPage />)
  const oldSignal = api.records.mock.calls[0][2] as AbortSignal
  context[field] = `${field}-new`
  view.rerender(<ExperimentsPage />)
  expect(oldSignal.aborted).toBe(true)
  await screen.findByRole('button', { name: 'Current project run' })
  await act(async () => old.resolve([run('Old project run')]))
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Old project run' })).not.toBeInTheDocument())
  expect(screen.getByRole('button', { name: 'Current project run' })).toBeVisible()
})
