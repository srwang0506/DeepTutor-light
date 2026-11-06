import { createRef } from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { expect, it, vi } from 'vitest'
import { FileInput } from '@/shared/ui/FileInput'

it('switches picker labels immediately while retaining the original filename and native input contract', async () => {
  const i18n = createInstance()
  await i18n.init({
    lng: 'en', fallbackLng: 'en',
    resources: {
      en: { translation: { 'Choose file': 'Choose file', 'No file selected': 'No file selected' } },
      zh: { translation: { 'Choose file': '选择文件', 'No file selected': '尚未选择文件' } },
    },
  })
  const ref = createRef<HTMLInputElement>()
  const changed = vi.fn()
  render(
    <I18nextProvider i18n={i18n}>
      <label>Source archive<FileInput ref={ref} name="archive" accept=".zip" multiple onChange={changed} /></label>
    </I18nextProvider>,
  )
  const input = screen.getByLabelText(/^Source archive/) as HTMLInputElement
  expect(input.labels?.[0]).toHaveTextContent('Source archive')
  expect(ref.current).toBe(input)
  expect(input).toHaveAttribute('accept', '.zip')
  expect(input).toHaveAttribute('name', 'archive')
  expect(input.multiple).toBe(true)
  expect(screen.getByText('No file selected')).toBeVisible()
  await act(async () => { await i18n.changeLanguage('zh') })
  expect(screen.getByText('选择文件')).toBeVisible()
  expect(screen.getByText('尚未选择文件')).toBeVisible()
  const source = new File(['source'], 'HOI 原始模板.zip', { type: 'application/zip' })
  fireEvent.change(input, { target: { files: [source] } })
  expect(changed).toHaveBeenCalledOnce()
  expect(changed.mock.calls[0][0].target.files[0]).toBe(source)
  expect(screen.getByText('HOI 原始模板.zip')).toBeVisible()
  await act(async () => { await i18n.changeLanguage('en') })
  expect(screen.getByText('Choose file')).toBeVisible()
  expect(screen.queryByText('选择文件')).not.toBeInTheDocument()
  expect(screen.getByText('HOI 原始模板.zip')).toBeVisible()
  fireEvent.change(input, { target: { files: [] } })
  expect(screen.getByText('No file selected')).toBeVisible()
})
