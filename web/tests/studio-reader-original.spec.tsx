import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import EditionReader from '@/components/studio/EditionReader'

const state = vi.hoisted(() => ({
  search: new URLSearchParams(), studio: vi.fn(), records: vi.fn(), post: vi.fn(),
}))
vi.mock('@/hooks/useRecoveryPrincipal', () => ({ useRecoveryPrincipal: () => 'user:a' }))
vi.mock('@/hooks/useActiveWorkspaceId', () => ({ useActiveWorkspaceId: () => 'project' }))
vi.mock('@/hooks/useAuthStatus', () => ({ useAuthStatus: () => ({ loading: false }) }))
vi.mock('@/components/access/CapabilityAccessContext', () => ({ useCapabilityAccess: () => ({ has: () => false }) }))
vi.mock('react-i18next', () => ({ useTranslation: () => ({
  t: (key: string, values?: Record<string, unknown>) => key.replace(/{{(\w+)}}/g, (_, key) => String(values?.[key] ?? '')),
}) }))
vi.mock('next/navigation', () => ({ useSearchParams: () => state.search, useParams: () => ({ edition: 'paper' }) }))
vi.mock('next/link', () => ({ default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => <a href={href} {...props}>{children}</a> }))
vi.mock('next/dynamic', () => ({ default: () => () => null }))
vi.mock('@/lib/studio-api', async importOriginal => ({
  ...await importOriginal<typeof import('@/lib/studio-api')>(), studio: state.studio, records: state.records, post: state.post,
}))
vi.mock('@/components/reading/PdfDocumentView', () => ({ PdfDocumentView: ({ sourceUrl, onVisibleLocatorChange }: {
  sourceUrl: string; onVisibleLocatorChange?: (page: number) => void
}) => <><a href={sourceUrl}>Visible PDF</a><button onClick={() => onVisibleLocatorChange?.(3)}>Scroll to page 3</button></> }))

const edition = {
  id: 'paper', kind: 'edition', scope: '@library', revision: 2,
  data: { work_id: 'work', label: 'Original', files: [{ hash: 'original-hash', pages: 3, name: 'paper.pdf', outline: [] }] },
}
beforeEach(() => {
  state.search = new URLSearchParams('revision=2&page=2&file=original-hash')
  window.history.replaceState(null, '', `/library/paper?${state.search}`)
  state.records.mockReset().mockResolvedValue([])
  state.post.mockReset().mockResolvedValue({})
  state.studio.mockReset().mockImplementation(async (_workspace, route: string) =>
    route.startsWith('/record/') ? edition : { data: { title: 'Research paper' }, editions: [edition] })
})

it('does not silently replace a mismatched original in a frozen source link', async () => {
  state.search = new URLSearchParams('revision=2&page=2&file=unrelated-hash&dt_workspace=project&session=discussion')
  window.history.replaceState(null, '', `/library/paper?${state.search}#source`)
  const view = render(<EditionReader />)
  expect(await screen.findByRole('alert')).toHaveTextContent('链接中的原件不属于此版本')
  expect(screen.queryByRole('link', { name: 'Visible PDF' })).toBeNull()
  expect(state.post).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '打开此版本的原件' }))
  const visible = await screen.findByRole('link', { name: 'Visible PDF' })
  expect(visible.getAttribute('href')).toContain('/editions/paper/files/original-hash?revision=2')
  expect(screen.getByRole('spinbutton', { name: '跳到物理页' })).toHaveValue(1)
  expect(screen.queryByRole('alert')).toBeNull()
  const link = new URL(window.location.href)
  expect(Object.fromEntries(link.searchParams)).toEqual({
    revision: '2', page: '1', file: 'original-hash', dt_workspace: 'project', session: 'discussion',
  })
  expect(link.hash).toBe('#source')
  view.unmount()
  state.search = new URLSearchParams(link.search)
  render(<EditionReader />)
  await screen.findByRole('link', { name: 'Visible PDF' })
  expect(screen.getByRole('spinbutton', { name: '跳到物理页' })).toHaveValue(1)
  expect(screen.queryByRole('alert')).toBeNull()
})

it('does not replace the URL when ordinary reading scroll changes the visible page', async () => {
  render(<EditionReader />)
  fireEvent.click(await screen.findByRole('button', { name: 'Scroll to page 3' }))
  expect(screen.getByRole('spinbutton', { name: '跳到物理页' })).toHaveValue(3)
  expect(new URL(window.location.href).searchParams.get('page')).toBe('2')
})

it('retries a failed original load without leaving the reader', async () => {
  state.studio.mockRejectedValueOnce(new Error('Temporary failure'))
  render(<EditionReader />)
  fireEvent.click(await screen.findByRole('button', { name: 'Retry' }))
  await screen.findByRole('link', { name: 'Visible PDF' })
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
  expect(state.studio).toHaveBeenCalledWith('project', '/record/paper?revision=2', {}, expect.any(AbortSignal))
})
