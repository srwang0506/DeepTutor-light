import { render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import ResearchWorkbench from '@/app/(utility)/research/page'
import { ensureLanguage, initI18n } from '@/i18n/init'
import { rememberWorkspaceId } from '@/lib/workspace-scope'

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
}))
vi.mock('@/features/capabilities/useCapabilityCatalog', () => ({
  useCapabilityFilter: () => () => false,
}))

afterEach(() => {
  rememberWorkspaceId('')
  window.history.replaceState(null, '', '/')
})

it('provides workspace-scoped Studio and original ecosystem links without a category sidebar', async () => {
  const i18n = initI18n('en')
  await ensureLanguage('en')
  await i18n.changeLanguage('en')
  const workspace = 'research / one'
  window.history.replaceState(null, '', `/research?${new URLSearchParams({ dt_workspace: workspace })}`)
  render(<ResearchWorkbench />)

  expect(screen.getByRole('heading', { level: 1, name: 'Research' })).toBeVisible()
  expect(screen.queryByRole('tablist')).toBeNull()
  const destinations = [
    ['Evidence & comparisons', '/studio/evidence'],
    ['Experiments', '/studio/experiments'],
    ['Manuscript writing', '/studio/writing'],
    ['Remote connections', '/studio/connections'],
    ['Paper library', '/learning/reading'],
    [i18n.t('Co-Writer'), '/co-writer'],
    ['Partners', '/partners'],
    ['Tools & integrations', '/space'],
    ['Calendar', '/calendar'],
    ['Visualize', '/chat'],
  ] as const
  for (const [title, pathname] of destinations) {
    const heading = screen.getByRole('heading', { level: 3, name: title })
    const link = heading.closest('a')
    expect(link, title).not.toBeNull()
    expect(link, title).toBeVisible()
    const url = new URL(link!.getAttribute('href')!, window.location.origin)
    expect(url.pathname, title).toBe(pathname)
    expect(url.searchParams.get('dt_workspace'), title).toBe(workspace)
    if (title === 'Visualize') expect(url.searchParams.get('capability')).toBe('visualize')
  }
  const tools = screen.getByRole('heading', { level: 3, name: 'Tools & integrations' }).closest('a')!
  expect(within(tools).getByText('Skills, MCP tools and the wider DeepTutor ecosystem.')).toBeVisible()
})
