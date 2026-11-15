import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import LibraryPage from '@/components/studio/LibraryPage'

const api = vi.hoisted(() => ({ studio: vi.fn(), post: vi.fn(), principal: 'user:one' }))
vi.mock('@/lib/studio-api', async importOriginal => ({
  ...await importOriginal<typeof import('@/lib/studio-api')>(), studio: api.studio, post: api.post,
}))
vi.mock('@/hooks/useActiveWorkspaceId', () => ({ useActiveWorkspaceId: () => 'study' }))
vi.mock('@/hooks/useRecoveryPrincipal', () => ({ useRecoveryPrincipal: () => api.principal }))
vi.mock('@/lib/recovery-principal', () => ({ recoveryPrincipal: () => api.principal }))
vi.mock('@/lib/workspace-scope', () => ({ activeWorkspaceId: () => 'study', scopedUrl: (url: string) => url }))
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))
vi.mock('@/components/studio/IntegrationSources', () => ({ default: () => null }))
vi.mock('@/components/studio/DiscoveryPanel', () => ({ default: () => null }))
vi.mock('@/components/studio/BibliographyExport', () => ({ default: () => null }))

const edition = { id: 'edition-one', revision: 1, data: { files: [] } }
const work = (id: string) => ({ id, data: { title: id, authors: [], tags: [] }, editions: [] })

beforeEach(() => {
  api.principal = 'user:one'
  api.post.mockReset().mockResolvedValue(edition)
  api.studio.mockReset().mockResolvedValue({ works: [], next_cursor: '' })
})

async function openCollection(withFile = true) {
  render(<LibraryPage />)
  await screen.findByText('还没有资料')
  fireEvent.click(screen.getByRole('button', { name: '添加资料' }))
  fireEvent.change(screen.getByLabelText('标题'), { target: { value: 'A paper' } })
  if (withFile) fireEvent.change(screen.getByLabelText('PDF 原文'), {
    target: { files: [new File(['%PDF-fixture'], 'paper.pdf', { type: 'application/pdf' })] },
  })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '保存到资料库' })) })
}

it('retries a failed PDF upload on its saved edition without creating another work', async () => {
  let uploads = 0
  api.studio.mockImplementation(async (_workspace, path) => {
    if (path.endsWith('/pdf')) {
      if (++uploads === 1) throw new Error('Upload interrupted')
      return { ...edition, revision: 2 }
    }
    return { works: [], next_cursor: '' }
  })
  await openCollection()
  expect((await screen.findByRole('alert')).textContent).toBe('Upload interrupted')
  expect(screen.getByLabelText('标题')).toBeDisabled()
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '继续保存' })) })
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(api.post).toHaveBeenCalledTimes(1)
  expect(uploads).toBe(2)
  expect(api.studio.mock.calls.filter(([, path]) => path.endsWith('/pdf')).every(
    ([, path]) => path === '/editions/edition-one/pdf',
  )).toBe(true)
})

it('reuses the request identity after a lost collection response and separates refresh failures', async () => {
  api.post.mockRejectedValueOnce(new Error('Response lost')).mockResolvedValueOnce(edition)
  await openCollection(false)
  expect(await screen.findByText('Response lost')).toBeVisible()
  api.studio.mockRejectedValue(new Error('List unavailable'))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '继续保存' })) })
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(api.post.mock.calls[0][2]).toEqual(api.post.mock.calls[1][2])
  expect(api.post.mock.calls[0][2].request_id).toBeTruthy()
  expect(await screen.findByText('List unavailable')).toBeVisible()
  api.studio.mockResolvedValue({ works: [work('Saved paper')], next_cursor: '' })
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  expect(await screen.findByText('Saved paper')).toBeVisible()
  expect(api.post).toHaveBeenCalledTimes(2)
})

it('does not show a failed list as an empty library and can recover in place', async () => {
  api.studio.mockRejectedValueOnce(new Error('Offline'))
  render(<LibraryPage />)
  expect(await screen.findByText('Offline')).toBeVisible()
  expect(screen.queryByText('还没有资料')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  expect(await screen.findByText('还没有资料')).toBeVisible()
  expect(screen.queryByText('Offline')).not.toBeInTheDocument()
})

it('does not continue uploading when the account changes during collection', async () => {
  let resolve!: (value: typeof edition) => void
  api.post.mockImplementation(() => new Promise(done => { resolve = done }))
  await openCollection()
  api.principal = 'user:two'
  await act(async () => { resolve(edition) })
  expect(api.studio.mock.calls.some(([, path]) => path.endsWith('/pdf'))).toBe(false)
})

it('ignores an old pagination response after the search changes', async () => {
  let resolve!: (value: unknown) => void
  api.studio.mockImplementation(async (_workspace, path: string) => {
    if (path.includes('cursor=next')) return new Promise(done => { resolve = done })
    return path.includes('q=new')
      ? { works: [work('New result')], next_cursor: '' }
      : { works: [work('Old result')], next_cursor: 'next' }
  })
  render(<LibraryPage />)
  fireEvent.click(await screen.findByRole('button', { name: '加载更多资料' }))
  fireEvent.change(screen.getByLabelText('搜索资料'), { target: { value: 'new' } })
  expect(await screen.findByText('New result')).toBeVisible()
  await act(async () => { resolve({ works: [work('Late old result')], next_cursor: '' }) })
  expect(screen.queryByText('Late old result')).not.toBeInTheDocument()
  expect(screen.queryByText('Old result')).not.toBeInTheDocument()
})
