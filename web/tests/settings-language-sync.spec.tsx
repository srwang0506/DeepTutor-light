import { StrictMode, useEffect } from 'react'
import { act, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { useTranslation } from 'react-i18next'
import { AppShellProvider, useAppShell } from '@/context/AppShellContext'
import { LANGUAGE_STORAGE_KEY, RESPONSE_LANGUAGE_STORAGE_KEY } from '@/context/app-shell-storage'
import { I18nClientBridge } from '@/i18n/I18nClientBridge'
import { SettingsProvider, useSettings } from '@/features/settings/store/SettingsStore'
import { apiFetch } from '@/lib/api'
import { initI18n } from '@/i18n/init'

vi.mock('@/lib/auth', () => ({ fetchAuthStatus: vi.fn(async () => null) }))
vi.mock('@/lib/api', () => ({ apiFetch: vi.fn(), apiUrl: (path: string) => path }))
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/lib/llm-options', () => ({ invalidateLLMOptionsCache: vi.fn() }))

let settings: ReturnType<typeof useSettings>
function Probe() {
  const { language } = useAppShell()
  const { i18n } = useTranslation()
  const value = useSettings()
  useEffect(() => {
    settings = value
  }, [value])
  return (
    <>
      <output data-testid="shell">{language}</output>
      <output data-testid="locale">{i18n.language}</output>
      <output data-testid="select">{value.language}</output>
      <output data-testid="dirty">{value.draftState}</output>
    </>
  )
}

beforeEach(async () => {
  localStorage.clear()
  await initI18n().changeLanguage('en')
  localStorage.setItem(LANGUAGE_STORAGE_KEY, 'en')
  localStorage.setItem(RESPONSE_LANGUAGE_STORAGE_KEY, 'en')
  vi.mocked(apiFetch).mockImplementation(
    async url =>
      ({
        ok: true,
        status: 200,
        json: async () =>
          String(url) === '/api/settings'
            ? { ui: { theme: 'snow', language: 'zh', response_language: 'fr' } }
            : String(url) === '/api/settings/draft'
              ? { draft: null }
              : {},
      }) as Response
  )
})

it.each([false, true])(
  'reconciles saved languages without applying drafts (StrictMode: %s)',
  async strict => {
    const content = (
      <AppShellProvider>
        <I18nClientBridge>
          <SettingsProvider>
            <Probe />
          </SettingsProvider>
        </I18nClientBridge>
      </AppShellProvider>
    )
    render(strict ? <StrictMode>{content}</StrictMode> : content)
    await waitFor(() => {
      expect(screen.getByTestId('select')).toHaveTextContent('zh')
      expect(screen.getByTestId('shell')).toHaveTextContent('zh')
      expect(screen.getByTestId('locale')).toHaveTextContent('zh')
    })
    expect(localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('zh')
    expect(localStorage.getItem(RESPONSE_LANGUAGE_STORAGE_KEY)).toBe('fr')
    expect(settings.responseLanguage).toBe('fr')
    expect(settings.draftState).toBe('clean')
    await act(async () => settings.updateLanguage('en'))
    expect(settings.language).toBe('en')
    expect(settings.responseLanguage).toBe('fr')
    expect(screen.getByTestId('shell')).toHaveTextContent('zh')
    expect(screen.getByTestId('locale')).toHaveTextContent('zh')
    expect(localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe('zh')
    expect(localStorage.getItem(RESPONSE_LANGUAGE_STORAGE_KEY)).toBe('fr')
    expect(settings.draftState).toBe('unsaved')
  }
)
