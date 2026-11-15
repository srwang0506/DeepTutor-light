import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import Continuations from '@/components/studio/Continuations'
import ObjectPage from '@/components/studio/ObjectPage'
import WritingPage from '@/components/studio/WritingPage'
import { browserStorage } from '@/shared/storage'
import { manuscriptDraftKey, manuscriptDraftEntries, readManuscriptDraft } from '@/lib/manuscript-draft'

const state = vi.hoisted(() => ({
  principal: 'user:a' as string | null,
  workspace: 'project',
  search: new URLSearchParams(),
  studio: vi.fn(), records: vi.fn(), post: vi.fn(),
}))
vi.mock('@/lib/recovery-principal', () => ({
  recoveryPrincipal: () => state.principal, RECOVERY_PRINCIPAL_CHANGED: 'test-principal-change',
}))
vi.mock('@/hooks/useActiveWorkspaceId', () => ({ useActiveWorkspaceId: () => state.workspace }))
vi.mock('react-i18next', () => ({ useTranslation: () => ({
  t: (key: string, values?: Record<string, unknown>) => key.replace(/{{(\w+)}}/g, (_, key) => String(values?.[key] ?? '')),
  i18n: { language: 'en' },
}) }))
vi.mock('next/navigation', () => ({
  useSearchParams: () => state.search, useParams: () => ({ id: 'activity' }),
  useRouter: () => ({ push: vi.fn() }), usePathname: () => '/studio/writing',
}))
vi.mock('next/link', () => ({ default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => <a href={href} {...props}>{children}</a> }))
vi.mock('@/lib/studio-api', async importOriginal => ({
  ...await importOriginal<typeof import('@/lib/studio-api')>(),
  studio: state.studio, records: state.records, post: state.post,
}))
vi.mock('@/components/quiz/StudyQuizViewer', () => ({ default: () => null }))
vi.mock('@/components/studio/WritingTools', () => ({ default: () => null }))
vi.mock('@/components/studio/WritingAssistant', () => ({ default: () => null }))
vi.mock('@/components/reading/PdfDocumentView', () => ({ PdfDocumentView: () => null }))
vi.mock('next/dynamic', () => ({ default: () => ({ value, onChange }: {value: string; onChange: (value: string) => void}) =>
  <textarea aria-label="Manuscript source" value={value} onChange={event => onChange(event.target.value)} />,
}))

beforeEach(() => {
  state.principal = 'user:a'
  state.workspace = 'project'
  state.search = new URLSearchParams()
  state.studio.mockReset()
  state.records.mockReset().mockResolvedValue([])
  state.post.mockReset()
  window.sessionStorage.clear()
  URL.revokeObjectURL = vi.fn()
})

const resume = {
  id: 'resume', kind: 'resume', title: 'Saved paper', revision: 5,
  source: { id: 'paper', revision: 2, locator: { page: 7, blob_hash: 'frozen-file' } },
}

it('retries continuation loading and returns to the exact saved paper revision', async () => {
  state.studio.mockRejectedValueOnce(new Error('Offline')).mockResolvedValue({ items: [resume] })
  render(<Continuations workspace="project" />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Offline')
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  const link = await screen.findByRole('link', { name: /Saved paper/ })
  const url = new URL(link.getAttribute('href')!, 'http://localhost')
  expect(url.searchParams.get('revision')).toBe('2')
  expect(url.searchParams.get('file')).toBe('frozen-file')
  expect(url.searchParams.get('page')).toBe('7')
  expect(screen.queryByRole('alert')).toBeNull()
})

it('drops project continuations immediately and ignores a late result after an account switch', async () => {
  let resolveOld!: (value: {items: typeof resume[]}) => void
  state.studio.mockResolvedValueOnce({ items: [resume] })
    .mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
    .mockResolvedValue({ items: [{ ...resume, title: 'Account B paper' }] })
  const view = render(<Continuations workspace="project" />)
  await screen.findByText('Saved paper')
  view.rerender(<Continuations workspace="other-project" />)
  expect(screen.queryByText('Saved paper')).toBeNull()
  await act(async () => {
    state.principal = 'user:b'
    window.dispatchEvent(new Event('test-principal-change'))
  })
  await screen.findByText('Account B paper')
  await act(async () => { resolveOld({ items: [{ ...resume, title: 'Late private result' }] }) })
  expect(screen.queryByText('Late private result')).toBeNull()
})

it('opens a practice source at its consumed revision and retries failed object loading', async () => {
  state.studio.mockRejectedValueOnce(new Error('Offline')).mockResolvedValue({
    id: 'activity', kind: 'activity', scope: 'project', revision: 3, latest_revision: 3,
    data: { title: 'Saved practice', source: resume.source },
  })
  render(<ObjectPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'Retry' }))
  const link = await screen.findByRole('link', { name: /回到原件/ })
  expect(link.getAttribute('href')).toContain('revision=2&file=frozen-file&page=7')
})

it('keeps draft conflicts scoped to the current account and decodes file paths safely', async () => {
  state.studio.mockResolvedValue({ items: [] })
  const path = 'sections/method:notes.tex'
  browserStorage.writeRaw('session', manuscriptDraftKey('user:a', 'project', 'paper', path), JSON.stringify({ conflict: true }))
  browserStorage.writeRaw('session', manuscriptDraftKey('user:b', 'project', 'paper', 'secret.tex'), JSON.stringify({ conflict: true }))
  render(<Continuations workspace="project" />)
  const link = await screen.findByRole('link', { name: new RegExp('method:notes') })
  expect(new URL(link.getAttribute('href')!, 'http://localhost').searchParams.get('path')).toBe(path)
  expect(screen.queryByText('secret.tex')).toBeNull()
  await act(async () => {
    state.principal = 'user:b'
    window.dispatchEvent(new Event('test-principal-change'))
  })
  expect(await screen.findByText('secret.tex')).toBeVisible()
  expect(screen.queryByText(path)).toBeNull()
})

it('recovers legacy local drafts only for single-user mode without resurrecting saved drafts', () => {
  const legacy = 'studio-draft:project:paper:main.tex'
  browserStorage.writeRaw('session', legacy, 'old local draft')
  expect(readManuscriptDraft('user:a', 'project', 'paper', 'main.tex')).toBeNull()
  expect(readManuscriptDraft('single-user', 'project', 'paper', 'main.tex')).toBe('old local draft')
  expect(browserStorage.readRaw('session', legacy)).toBeNull()
  browserStorage.removeRaw('session', manuscriptDraftKey('single-user', 'project', 'paper', 'main.tex'))
  expect(manuscriptDraftEntries('single-user', 'project')).toEqual([])
})

it('retains a different legacy draft without overwriting or later resurrecting it', () => {
  const legacy = 'studio-draft:project:paper:main.tex'
  const current = manuscriptDraftKey('single-user', 'project', 'paper', 'main.tex')
  browserStorage.writeRaw('session', legacy, 'legacy edit')
  browserStorage.writeRaw('session', current, 'newer account-owned edit')
  expect(readManuscriptDraft('single-user', 'project', 'paper', 'main.tex')).toBe('newer account-owned edit')
  expect(browserStorage.readRaw('session', legacy)).toBe('legacy edit')
  browserStorage.removeRaw('session', current)
  expect(readManuscriptDraft('single-user', 'project', 'paper', 'main.tex')).toBeNull()
  expect(browserStorage.readRaw('session', legacy)).toBe('legacy edit')
})

it('unmounts a manuscript on account change and never restores another account draft', async () => {
  state.search = new URLSearchParams('manuscript=paper')
  const document = { id: 'paper', revision: 1, data: { title: 'Paper', main_file: 'main.tex', engine: 'pdflatex' } }
  state.records.mockImplementation(async (_workspace, kind) => kind === 'manuscript' ? [document] : [])
  state.studio.mockImplementation(async (_workspace, route) => {
    if (route.endsWith('/files')) return { files: [{ path: 'main.tex', hash: 'hash', size: 4, editable: true }] }
    if (route.endsWith('/recovery')) return { conflicts: [] }
    if (route.includes('/file?')) return { path: 'main.tex', hash: 'hash', text: 'saved text' }
    throw new Error(route)
  })
  browserStorage.writeRaw('session', manuscriptDraftKey('user:a', 'project', 'paper', 'main.tex'), JSON.stringify({ hash: 'hash', text: 'account A draft' }))
  render(<WritingPage />)
  expect(await screen.findByRole('textbox', { name: 'Manuscript source' })).toHaveValue('account A draft')
  await act(async () => {
    state.principal = 'user:b'
    window.dispatchEvent(new Event('test-principal-change'))
  })
  await waitFor(() => expect(screen.getByRole('textbox', { name: 'Manuscript source' })).toHaveValue('saved text'))
  expect(browserStorage.readRaw('session', manuscriptDraftKey('user:a', 'project', 'paper', 'main.tex'))).toContain('account A draft')
})
