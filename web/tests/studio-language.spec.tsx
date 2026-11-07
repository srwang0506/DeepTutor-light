import { act, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useTranslation } from 'react-i18next'
import { StudioShell, ErrorNotice } from '@/components/studio/StudioShell'
import { studioLabel } from '@/components/studio/display'
import { ensureLanguage, initI18n } from '@/i18n/init'
import { offlineHref } from '@/lib/offline-study'
import { rememberWorkspaceId } from '@/lib/workspace-scope'
import english from '@/locales/en/app.json'
import englishWorkbench from '@/locales/en/workbench.json'
import { CategoryBody } from '@/components/sidebar/CategoryNavigation'

vi.mock('next/navigation', () => ({
  usePathname: () => '/studio/connections',
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: vi.fn() }),
}))
vi.mock('@/components/layout/AppShell', () => ({ useSidebarDrawer: () => null }))

afterEach(() => rememberWorkspaceId(''))

function WorkspaceExample() {
  const { t } = useTranslation()
  return (
    <StudioShell
      workspace="research-project"
      active="connections"
      title={t('远程连接')}
      description={t('连接 SSH 服务器或 GitHub 仓库，浏览文件并导入实验记录。')}
    >
      <CategoryBody
        nav={{ category: 'research', categories: [], visited: [], select: vi.fn(), allowedSurfaces: null }}
        collapsed={false}
        sessions={null}
      />
      <ErrorNotice message="Request failed. Check your input and try again." />
      <p data-testid="status-labels">
        {['pending', 'planned', 'configuration', 'committed', 'unauthorized']
          .map(value => studioLabel(t, value)).join(' · ')}
      </p>
    </StudioShell>
  )
}

it('keeps English interface resources free of Chinese source text', () => {
  // Language names retain their native spelling in the language picker.
  const untranslated = Object.entries({ ...english, ...englishWorkbench })
    .filter(([key, value]) => key !== 'language.chinese' && /[\u3400-\u9fff]/.test(value))
    .map(([key]) => key)
  expect(untranslated).toEqual([])
})

it('switches headings, metadata, errors and navigation together without a reload', async () => {
  const i18n = initI18n()
  await ensureLanguage('zh')
  await i18n.changeLanguage('en')
  rememberWorkspaceId('research-project')
  const view = render(<WorkspaceExample />)
  expect(view.container.textContent).not.toMatch(/[\u3400-\u9fff]/)
  expect(screen.getByRole('link', { name: 'Evidence & comparisons' })).toHaveAttribute(
    'href', '/studio/evidence?dt_workspace=research-project',
  )
  expect(screen.getByRole('heading', { level: 1, name: 'Remote connections' })).toBeVisible()
  await act(async () => { await i18n.changeLanguage('zh') })
  expect(screen.getByRole('heading', { name: '远程连接' })).toBeVisible()
  expect(screen.getByRole('link', { name: '证据与比较' })).toBeVisible()
  expect(screen.getAllByRole('link', { name: /远程(工作区|连接)/ })).toHaveLength(1)
  expect(view.container.querySelector('.studio-header .studio-eyebrow')).toBeNull()
  expect(view.container.querySelector('.studio-nav')).toBeNull()
  expect(screen.getByTestId('status-labels').textContent).not.toMatch(/[A-Za-z]/)
  expect(screen.getByRole('alert').textContent).toMatch(/[\u3400-\u9fff]/)
  await act(async () => { await i18n.changeLanguage('en') })
  expect(view.container.textContent).not.toMatch(/[\u3400-\u9fff]/)
})

it('carries the interface language into offline study while keeping workspace identity', async () => {
  const i18n = initI18n()
  await ensureLanguage('zh')
  for (const language of ['en', 'zh'] as const) {
    await i18n.changeLanguage(language)
    const url = new URL(offlineHref('project / one', 'user+one'), 'http://localhost')
    expect(url.searchParams.get('lang')).toBe(language)
    expect(url.searchParams.get('workspace')).toBe('project / one')
    expect(url.searchParams.get('owner')).toBe('user+one')
  }
})
