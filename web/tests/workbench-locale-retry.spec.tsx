import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useTranslation } from 'react-i18next'
import { expect, it, vi } from 'vitest'
import { I18nProvider } from '@/i18n/I18nProvider'
import { ensureLanguage } from '@/i18n/init'

vi.mock('@/i18n/init', async original => {
  const actual = await original<typeof import('@/i18n/init')>()
  return { ...actual, ensureLanguage: vi.fn(actual.ensureLanguage) }
})

function FeatureCopy() {
  const { t } = useTranslation()
  return <h1>{t('学习与科研工作台')}</h1>
}

it('offers a readable retry after a language load failure and then opens the requested interface', async () => {
  vi.mocked(ensureLanguage).mockRejectedValueOnce(new Error('Language chunk unavailable'))
  render(<I18nProvider language="zh"><FeatureCopy /></I18nProvider>)
  expect(await screen.findByRole('alert')).toHaveTextContent('暂时无法打开界面，请重试。')
  expect(screen.queryByRole('heading')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '重试' }))
  await screen.findByRole('heading', { name: '学习与科研工作台' })
  expect(ensureLanguage).toHaveBeenCalledTimes(2)
  expect(document.documentElement.lang).toBe('zh')
})
