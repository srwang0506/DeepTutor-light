import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import {
  CategoryBody,
  CategoryTabs,
  useCategoryNavigation,
} from '@/components/sidebar/CategoryNavigation'
import type { SessionSummary } from '@/lib/session-api'

const fixture = vi.hoisted(() => ({
  pathname: '/chat',
  push: vi.fn(),
  policy: null as null | { allowedSurfaces: string[] },
}))
vi.mock('next/navigation', () => ({
  usePathname: () => fixture.pathname,
  useSearchParams: () => new URLSearchParams(window.location.search),
  useRouter: () => ({ push: fixture.push }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en' } }),
}))
vi.mock('@/hooks/useAuthStatus', () => ({
  useAuthStatus: () => ({ learningPolicy: fixture.policy }),
}))
vi.mock('@/components/layout/AppShell', () => ({ useSidebarDrawer: () => null }))
vi.mock('@/features/capabilities/useCapabilityCatalog', () => ({
  useCapabilityFilter: () => () => false,
}))
vi.mock('@/hooks/useLearningIndex', () => ({
  useLearningIndex: () => ({
    loading: false, failed: [], retry: vi.fn(), sources: {
      books: [], practice: [], reading: [], watching: [], mastery: [{
        path_id: 'vectors', name: 'Vectors', updated_at: 50, content_workspace_id: 'physics', reviews: [],
        metadata: { status: 'active', goal: 'Vectors', created_at: 10 },
        map: { counts: { total: 2, mastered: 1 } },
      }],
    },
  }),
}))
vi.mock('@/hooks/usePartnerNavigation', () => ({
  usePartnerNavigation: () => ({
    loading: false, error: false, retry: vi.fn(), data: {
      partners: [], groups: [], unavailable: [], sessions: [{
        kind: 'partner', owner_id: 'ada', session_key: 'web-original',
        title: 'Existing partner conversation', owner_name: 'Ada', updated_at: 50,
      }],
    },
  }),
}))

const learningSession = {
  session_id: 'dot-products', title: 'Original learning conversation', created_at: 90,
  updated_at: 90, content_workspace_id: 'physics', preferences: {
    workspace_mode: 'mastery_path', mastery_path_id: 'vectors',
  },
} as SessionSummary

function Sidebar({ collapsed = false }: { collapsed?: boolean }) {
  const nav = useCategoryNavigation()
  return <>
    <CategoryTabs nav={nav} collapsed={collapsed} />
    <CategoryBody
      nav={nav}
      collapsed={collapsed}
      sessions={<p>Original chat history</p>}
      learningSessions={{
        rows: [learningSession], activeSessionId: 'dot-products',
        render: rows => <ul>{rows.map(row => <li key={row.session_id}>{row.title}</li>)}</ul>,
      }}
    />
  </>
}

beforeEach(() => {
  fixture.pathname = '/chat'
  fixture.policy = null
  fixture.push.mockClear()
  window.history.replaceState(null, '', '/chat?dt_workspace=physics')
})

it('opens the learning shelf and preserves its original records and conversations', async () => {
  render(<Sidebar />)
  expect(screen.getByRole('link', { name: 'Co-Writer' })).toHaveAttribute('href', '/co-writer')
  expect(screen.getByRole('link', { name: 'Projects' })).toHaveAttribute('href', '/projects')
  fireEvent.click(screen.getByRole('tab', { name: 'navigation.learning' }))
  expect(screen.getByRole('tab', { name: 'navigation.learning' })).toHaveAttribute('aria-selected', 'true')
  // Opening a shelf leaves the current conversation mounted, like the original shell.
  expect(fixture.push).not.toHaveBeenCalled()
  expect(screen.getByRole('link', { name: 'Study today' })).toHaveAttribute('href', '/today?dt_workspace=physics')
  expect(await screen.findByText('Original learning conversation')).toBeVisible()
  expect(screen.getByRole('link', { name: /Vectors/ })).toHaveAttribute('href', '/learning/mastery/vectors?dt_workspace=physics')
  expect(screen.getByRole('link', { name: 'navigation.createLearning' })).toHaveAttribute('href', '/learning/new')
})

it('makes partner history, personalization and resources independently reachable', async () => {
  render(<Sidebar />)
  fireEvent.click(screen.getByRole('tab', { name: 'navigation.partners' }))
  expect(await screen.findByRole('link', { name: /Existing partner conversation/ })).toHaveAttribute(
    'href', '/partners/ada?session=web-original',
  )
  fireEvent.click(screen.getByRole('tab', { name: 'navigation.personalization' }))
  expect(await screen.findByRole('link', { name: 'My Agents' })).toHaveAttribute('href', '/agents')
  fireEvent.click(screen.getByRole('tab', { name: 'navigation.resources' }))
  expect(await screen.findByRole('link', { name: 'Library' })).toHaveAttribute('href', '/library?dt_workspace=physics')
  expect(screen.getByRole('link', { name: 'Favorites' })).toHaveAttribute('href', '/notebooks')
})

it('keeps the research shelf focused on research and preserves the workspace in links', () => {
  fixture.pathname = '/studio/experiments'
  render(<Sidebar />)
  expect(screen.getByRole('tab', { name: 'Research' })).toHaveAttribute('aria-selected', 'true')
  const panel = within(screen.getByRole('tabpanel'))
  expect(panel.getByRole('link', { name: '实验记录' })).toHaveAttribute('href', '/studio/experiments?dt_workspace=physics')
  expect(panel.getByRole('link', { name: 'Deep research' })).toHaveAttribute('href', '/chat?capability=deep_research&dt_workspace=physics')
  expect(panel.queryByRole('link', { name: 'Practice & records' })).toBeNull()
  expect(panel.queryByRole('link', { name: 'Partners' })).toBeNull()
  expect(panel.queryByRole('link', { name: 'Explore the ecosystem' })).toBeNull()
})

it('retains keyboard selection in the expanded shelf and scoped navigation in the collapsed rail', async () => {
  const view = render(<Sidebar />)
  const home = screen.getByRole('tab', { name: 'navigation.home' })
  fireEvent.keyDown(home, { key: 'ArrowRight' })
  expect(screen.getByRole('tab', { name: 'navigation.learning' })).toHaveFocus()
  expect(screen.getByRole('tab', { name: 'navigation.learning' })).toHaveAttribute('aria-selected', 'true')
  expect(fixture.push).not.toHaveBeenCalled()
  await screen.findByText('Original learning conversation')
  view.unmount()
  render(<Sidebar collapsed />)
  fireEvent.click(screen.getByRole('tab', { name: 'Research' }))
  expect(fixture.push).toHaveBeenCalledExactlyOnceWith('/research?dt_workspace=physics')
})

it.each([
  [['reading'], ['navigation.learning']],
  [['books'], ['navigation.learning']],
  [['chat'], ['navigation.home', 'navigation.learning']],
  [[], []],
] as [string[], string[]][] )('restricts available shelves to the allowed learning surfaces %j', (allowed, expected) => {
  fixture.policy = { allowedSurfaces: allowed }
  render(<Sidebar />)
  expect(screen.queryAllByRole('tab').map(tab => tab.getAttribute('aria-label'))).toEqual(expected)
  expect(screen.queryByRole('link', { name: 'Projects' })).toBeNull()
  expect(screen.queryByRole('tab', { name: 'Research' })).toBeNull()
  expect(screen.queryByRole('tab', { name: 'navigation.partners' })).toBeNull()
  expect(screen.queryByRole('tab', { name: 'navigation.personalization' })).toBeNull()
})
