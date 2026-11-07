import { act, render, screen } from '@testing-library/react'
import { useTranslation } from 'react-i18next'
import { expect, it } from 'vitest'
import { I18nProvider } from '@/i18n/I18nProvider'
import { ensureLanguage, initI18n, isLanguageReady } from '@/i18n/init'

function FeatureCopy() {
  const { t } = useTranslation()
  return <h1>{t('远程连接')}</h1>
}

it('waits for feature translations before exposing the interface and shares concurrent loads', async () => {
  const i18n = initI18n('en')
  expect(i18n.hasResourceBundle('en', 'app')).toBe(true)
  expect(isLanguageReady('en')).toBe(false)
  const view = render(<I18nProvider language="en"><FeatureCopy /></I18nProvider>)
  expect(screen.queryByText('远程连接')).not.toBeInTheDocument()
  expect(view.container.querySelector('[aria-busy="true"]')).not.toBeNull()
  await screen.findByRole('heading', { name: 'Remote connections' })
  expect(isLanguageReady('en')).toBe(true)
  await act(async () => {
    await Promise.all([ensureLanguage('zh'), ensureLanguage('en'), ensureLanguage('zh')])
  })
  expect(i18n.getFixedT('zh', 'app')('远程连接')).toBe('远程连接')
  expect(i18n.getFixedT('en', 'app')('远程连接')).toBe('Remote connections')
  view.rerender(<I18nProvider language="zh"><FeatureCopy /></I18nProvider>)
  await screen.findByRole('heading', { name: '远程连接' })
  expect(document.documentElement.lang).toBe('zh')
  await ensureLanguage('fr')
  expect(i18n.getFixedT('fr', 'app')('新建或导入 LaTeX 工程，编辑、编译并导出论文。')).toBe(
    'Create or import a LaTeX project, then edit, compile and export your paper.',
  )
})
