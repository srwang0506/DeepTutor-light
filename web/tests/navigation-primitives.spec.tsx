import { render, screen } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { BookOpen } from 'lucide-react'
import { OverviewRow } from '@/components/sidebar/NavigationPrimitives'

const fixture = vi.hoisted(() => ({
  pathname: '/today',
  search: 'dt_workspace=physics&plan=current',
}))
vi.mock('next/navigation', () => ({
  usePathname: () => fixture.pathname,
  useSearchParams: () => new URLSearchParams(fixture.search),
}))
vi.mock('@/components/layout/AppShell', () => ({ useSidebarDrawer: () => null }))

beforeEach(() => {
  fixture.pathname = '/today'
  fixture.search = 'dt_workspace=physics&plan=current'
})

it.each([
  ['/today?dt_workspace=physics', true],
  ['/today?workspace=physics', true],
  ['/today?dt_workspace=physics&plan=current', true],
  ['/today?dt_workspace=physics&plan=next', false],
  ['/today?dt_workspace=chemistry', false],
  ['/today?dt_workspace=', false],
  ['/today-archive?dt_workspace=physics', false],
] as [string, boolean][] )('highlights the scoped overview only for its actual target %s', (href, selected) => {
  const view = render(<OverviewRow href={href} label="Study today" icon={BookOpen} />)
  const link = screen.getByRole('link', { name: 'Study today' })
  expect(link.getAttribute('aria-current')).toBe(selected ? 'page' : null)
  expect(view.container.querySelector('.dt-overview-row')?.classList.contains('is-active')).toBe(selected)
  expect(link).toHaveAttribute('href', href)
})

it('updates the active overview when only the workspace or target query changes', () => {
  fixture.pathname = '/research'
  fixture.search = 'dt_workspace=physics&view=papers'
  const row = <OverviewRow href="/research?dt_workspace=physics&view=papers" label="Research" icon={BookOpen} />
  const view = render(row)
  const link = () => screen.getByRole('link', { name: 'Research' })
  expect(link()).toHaveAttribute('aria-current', 'page')
  fixture.search = 'dt_workspace=biology&view=papers'
  view.rerender(<OverviewRow href="/research?dt_workspace=physics&view=papers" label="Research" icon={BookOpen} />)
  expect(link()).not.toHaveAttribute('aria-current')
  fixture.search = 'dt_workspace=physics&view=experiments'
  view.rerender(<OverviewRow href="/research?dt_workspace=physics&view=papers" label="Research" icon={BookOpen} />)
  expect(link()).not.toHaveAttribute('aria-current')
})

it('does not highlight a shelf overview on one of its detail pages', () => {
  fixture.pathname = '/research/papers/paper-one'
  fixture.search = 'dt_workspace=physics'
  const view = render(<OverviewRow href="/research?dt_workspace=physics" label="Research" icon={BookOpen} />)
  expect(screen.getByRole('link', { name: 'Research' })).not.toHaveAttribute('aria-current')
  expect(view.container.querySelector('.dt-overview-row')).not.toHaveClass('is-active')
})
