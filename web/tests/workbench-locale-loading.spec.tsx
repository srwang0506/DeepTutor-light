import { act, render, screen } from '@testing-library/react'
import { useTranslation } from 'react-i18next'
import { expect, it } from 'vitest'
import { I18nProvider } from '@/i18n/I18nProvider'
import { ensureLanguage, initI18n, isLanguageReady } from '@/i18n/init'

function FeatureCopy() {
  const { t } = useTranslation()
  return <h1>{t('学习与科研工作台')}</h1>
}

it('waits for feature translations before exposing the interface and shares concurrent loads', async () => {
  const i18n = initI18n('en')
  expect(i18n.hasResourceBundle('en', 'app')).toBe(true)
  expect(isLanguageReady('en')).toBe(false)
  const view = render(<I18nProvider language="en"><FeatureCopy /></I18nProvider>)
  expect(screen.queryByText('学习与科研工作台')).not.toBeInTheDocument()
  expect(view.container.querySelector('[aria-busy="true"]')).not.toBeNull()
  await screen.findByRole('heading', { name: 'Personal study and research studio' })
  expect(isLanguageReady('en')).toBe(true)
  await act(async () => {
    await Promise.all([ensureLanguage('zh'), ensureLanguage('en'), ensureLanguage('zh')])
  })
  expect(i18n.getFixedT('zh', 'app')('学习与科研工作台')).toBe('学习与科研工作台')
  expect(i18n.getFixedT('en', 'app')('学习与科研工作台')).toBe('Personal study and research studio')
  view.rerender(<I18nProvider language="zh"><FeatureCopy /></I18nProvider>)
  await screen.findByRole('heading', { name: '学习与科研工作台' })
  expect(document.documentElement.lang).toBe('zh')
})
