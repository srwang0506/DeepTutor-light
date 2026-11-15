import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import StudioReferencePicker from '@/components/chat/StudioReferencePicker'
import type { StudioSourceItem } from '@/lib/studio-references'

const api = vi.hoisted(() => ({ studio: vi.fn() }))
vi.mock('@/lib/studio-api', () => api)
vi.mock('@/hooks/useRecoveryPrincipal', () => ({ useRecoveryPrincipal: () => 'local-test' }))
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))

const source: StudioSourceItem = {
  title: 'QPIC',
  description: '',
  pages: 12,
  href: '/library/paper?revision=2',
  ref: {
    scope: '@library',
    kind: 'edition',
    id: 'paper',
    revision: 2,
    locator: { blob_hash: 'a'.repeat(64) },
  },
}
beforeEach(() => api.studio.mockReset().mockResolvedValue({ items: [source] }))

it('updates a selected paper when its page changes and blocks invalid page confirmation', async () => {
  const apply = vi.fn()
  render(<StudioReferencePicker workspace="hoi" initial={[]} onApply={apply} onClose={() => {}} />)
  const checked = await screen.findByRole('checkbox', { name: /QPIC/ })
  fireEvent.click(checked)
  const page = screen.getByRole('spinbutton')
  fireEvent.change(page, { target: { value: '3' } })
  expect(checked).toBeChecked()
  fireEvent.click(screen.getByRole('button', { name: 'Use sources' }))
  expect(apply).toHaveBeenLastCalledWith([
    expect.objectContaining({
      ref: { ...source.ref, locator: { ...source.ref.locator, page: 3 } },
      href: expect.stringContaining('page=3'),
    }),
  ])
  fireEvent.change(page, { target: { value: '99' } })
  expect(screen.getByRole('button', { name: 'Use sources' })).toBeDisabled()
  expect(page).toHaveAttribute('aria-invalid', 'true')
  fireEvent.change(page, { target: { value: '' } })
  expect(screen.getByRole('button', { name: 'Use sources' })).toBeEnabled()
  fireEvent.click(screen.getByRole('button', { name: 'Use sources' }))
  expect(apply).toHaveBeenLastCalledWith([expect.objectContaining({ ref: source.ref })])
})

it('restores a pinned page when reopening and can remove that paper as one selection', async () => {
  const apply = vi.fn()
  render(
    <StudioReferencePicker
      workspace="hoi"
      initial={[{ ...source, ref: { ...source.ref, locator: { ...source.ref.locator, page: 8 } } }]}
      onApply={apply}
      onClose={() => {}}
    />
  )
  const checked = await screen.findByRole('checkbox', { name: /QPIC/ })
  expect(checked).toBeChecked()
  expect(screen.getByRole('spinbutton')).toHaveValue(8)
  fireEvent.click(checked)
  fireEvent.click(screen.getByRole('button', { name: 'Use sources' }))
  expect(apply).toHaveBeenCalledWith([])
})
