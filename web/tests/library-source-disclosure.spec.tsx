import { fireEvent, render, screen, within } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import IntegrationSources from '@/components/studio/IntegrationSources'

const api = vi.hoisted(() => ({ studio: vi.fn(), post: vi.fn() }))
vi.mock('@/lib/studio-api', () => api)
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) => key.replace(
      /\{\{(\w+)\}\}/g, (_, name: string) => String(values?.[name] ?? ''),
    ),
    i18n: { language: 'en' },
  }),
}))

it('keeps source problems visible while collapsed and opens connection management without nested dialogs', async () => {
  api.studio.mockResolvedValue({ sources: ['unauthorized', 'retrying', 'error'].map((state) => ({
    id: state,
    revision: 1,
    data: {
      provider: 'zotero', title: state, state, enabled: state !== 'unauthorized',
      library_type: 'users', library_id: state, path: '', cursor: {}, interval_seconds: 300,
    },
  })) })
  const view = render(<IntegrationSources workspace="research" provider="zotero" compact />)
  const disclosure = view.container.querySelector('details')!
  const summary = disclosure.querySelector('summary')!
  expect(await within(summary).findByText('已连接 2 个来源')).toBeVisible()
  expect(disclosure).not.toHaveAttribute('open')
  expect(within(summary).getByRole('status')).toHaveTextContent('需要重新授权 · 等待重试 · 同步失败')
  expect(screen.getByRole('button', { name: '连接来源', hidden: true })).not.toBeVisible()

  fireEvent.click(summary)
  expect(disclosure).toHaveAttribute('open')
  fireEvent.click(screen.getByRole('button', { name: '连接来源' }))
  const dialog = screen.getByRole('dialog', { name: '连接只读来源' })
  expect(screen.getAllByRole('dialog')).toHaveLength(1)
  expect(within(dialog).getByLabelText('Zotero Library ID')).toBeVisible()
  expect(within(dialog).getByLabelText('只读 API Key', { exact: false })).toBeVisible()
  fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

  fireEvent.click(summary)
  expect(disclosure).not.toHaveAttribute('open')
  expect(within(summary).getByRole('status')).toBeVisible()
  expect(api.post).not.toHaveBeenCalled()
})
