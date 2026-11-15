import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import LibraryWorkRow from '@/components/studio/LibraryWorkRow'
import type { Edition, Work } from '@/lib/studio-api'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, values?: { count?: number }) => key.replace('{{count}}', String(values?.count ?? '')) }),
}))

function edition(id: string, label: string, hasFile = true): Edition {
  return {
    id, kind: 'edition', scope: 'account', revision: 1, created: 1, updated: 1,
    data: {
      work_id: 'work', label, doi: '', arxiv: '', citekey: `${id}-key`, url: '', venue: '', full_text: '',
      files: hasFile ? [{ hash: id, name: `${id}.pdf`, pages: 1, size: 20, outline: [] }] : [],
    },
  }
}

function work(editions: Edition[]): Work {
  return {
    id: 'work', kind: 'work', scope: 'account', revision: 1, created: 1, updated: 1,
    data: { title: 'Human–Object Interaction', authors: ['Alex Smith'], year: 2024, kind: 'paper', tags: [], abstract: '' },
    editions,
  }
}

it('opens a single edition directly and keeps secondary actions keyboard accessible', async () => {
  const user = userEvent.setup()
  const single = edition('camera-ready', 'Camera-ready')
  const onJoin = vi.fn()
  const onAddEdition = vi.fn()
  render(<>
    <LibraryWorkRow work={work([single])} workspace="hoi" busy={false} onAttach={vi.fn()} onJoin={onJoin} onAddEdition={onAddEdition} />
    <button type="button">Next control</button>
  </>)

  for (const name of ['Human–Object Interaction', '阅读']) {
    expect(screen.getByRole('link', { name })).toHaveAttribute('href', '/library/camera-ready?dt_workspace=hoi')
  }
  expect(screen.getByText('Camera-ready')).toBeVisible()
  expect(screen.queryByText('作者待补充')).not.toBeInTheDocument()
  const trigger = screen.getByRole('button', { name: '更多操作' })
  fireEvent.click(trigger)
  expect(screen.getByRole('menuitem', { name: '加入项目' })).toHaveFocus()
  fireEvent.click(screen.getByRole('menuitem', { name: '加入项目' }))
  expect(onJoin).toHaveBeenCalledExactlyOnceWith(single)
  expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  expect(trigger).toHaveFocus()
  fireEvent.click(trigger)
  fireEvent.keyDown(document, { key: 'ArrowDown' })
  expect(screen.getByRole('menuitem', { name: '添加版本' })).toHaveFocus()
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  expect(trigger).toHaveFocus()

  fireEvent.click(trigger)
  fireEvent.keyDown(document, { key: 'ArrowDown' })
  expect(screen.getByRole('menuitem', { name: '添加版本' })).toHaveFocus()
  await user.tab()
  const next = screen.getByRole('button', { name: 'Next control' })
  expect(next).toHaveFocus()
  expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  const arrow = new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true })
  fireEvent(next, arrow)
  expect(arrow.defaultPrevented).toBe(false)
  expect(next).toHaveFocus()
})

it('requires an explicit edition choice and preserves every version’s citation and source details', () => {
  const published = { ...edition('published', 'Published version'), project_role: 'reading' }
  const preprint = edition('preprint', '预印本', false)
  preprint.data.citekey_aliases = ['legacy-key']
  preprint.data.bibliographic_sources = [{ format: 'bibtex', record: { title: 'Imported title' } }]
  preprint.data.bibliographic_conflicts = [{ field: 'title', retained: 'Human–Object Interaction', incoming: 'Imported title', source: 'bibtex' }]
  const onAttach = vi.fn()
  const onJoin = vi.fn()
  const { container } = render(<LibraryWorkRow work={work([published, preprint])} workspace="another-project" busy={false} onAttach={onAttach} onJoin={onJoin} onAddEdition={vi.fn()} />)

  expect(screen.queryByRole('link', { name: 'Human–Object Interaction' })).not.toBeInTheDocument()
  const versions = container.querySelector<HTMLDetailsElement>('.library-work-versions')!
  expect(versions).not.toHaveAttribute('open')
  expect(versions.querySelector('summary')).toHaveTextContent('2 个版本')
  fireEvent.click(versions.querySelector('summary')!)
  expect(versions).toHaveAttribute('open')
  expect(screen.getByRole('link', { name: 'Published version' })).toHaveAttribute('href', '/library/published?dt_workspace=another-project')
  expect(screen.getByRole('link', { name: '阅读' })).toHaveAttribute('href', '/library/published?dt_workspace=another-project')
  fireEvent.click(screen.getByRole('button', { name: '添加原文' }))
  fireEvent.click(screen.getByRole('button', { name: '加入项目' }))
  expect(onAttach).toHaveBeenCalledExactlyOnceWith(preprint)
  expect(onJoin).toHaveBeenCalledExactlyOnceWith(preprint)

  const editionRows = [...container.querySelectorAll<HTMLElement>('.library-work-edition')]
  expect(editionRows).toHaveLength(2)
  expect(within(editionRows[0]).getByText('本项目')).toBeVisible()
  const details = editionRows[1].querySelector<HTMLDetailsElement>('.library-work-bibliography')!
  fireEvent.click(details.querySelector('summary')!)
  expect(within(details).getByText('引用键：preprint-key · legacy-key')).toBeVisible()
  expect(within(details).getByText('保留值：Human–Object Interaction')).toBeVisible()
  expect(within(details).getByText('导入值：Imported title')).toBeVisible()
  const source = details.querySelector<HTMLDetailsElement>('.library-work-source')!
  fireEvent.click(source.querySelector('summary')!)
  expect(within(source).getByText(/"title": "Imported title"/)).toBeVisible()
  const publishedDetails = editionRows[0].querySelector<HTMLDetailsElement>('.library-work-bibliography')!
  fireEvent.click(publishedDetails.querySelector('summary')!)
  expect(within(publishedDetails).getByText('引用键：published-key')).toBeVisible()
})
