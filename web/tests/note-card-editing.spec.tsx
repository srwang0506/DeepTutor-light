import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import NoteFlashcards from '@/features/notes/components/NoteFlashcards'
vi.mock('react-i18next', () => ({ useTranslation: () => ({ i18n: { language: 'zh' } }) }))
afterEach(cleanup)
it('preserves the old identity through an answer edit and never grades a flip', () => {
  const change = vi.fn()
  const data = JSON.stringify({ cards: [{ front: 'Power?', back: '1 mW', _study: { id: 'legacy:hash', legacy_guid: 'legacy:hash' } }] })
  render(<NoteFlashcards data={data} readOnly={false} onChange={change}/>)
  fireEvent.click(screen.getByRole('button', { name: '翻面' }))
  expect(change).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '编辑' }))
  fireEvent.change(screen.getByLabelText('答案'), { target: { value: '1 MW' } })
  fireEvent.click(screen.getByRole('button', { name: '保存卡片' }))
  const card = JSON.parse(change.mock.calls[0][0].data).cards[0]
  expect(card.back).toBe('1 MW')
  expect(card._study.id).toBe('legacy:hash')
  expect(card._study.legacy_guid).toBe('legacy:hash')
})
it('new cards receive distinct identities before the first edit', () => {
  const change = vi.fn()
  render(<NoteFlashcards data={JSON.stringify({ cards: [{ front: 'Q', back: 'A' }, { front: 'Q', back: 'A' }] })} readOnly={false} onChange={change}/>)
  const cards = JSON.parse(change.mock.calls[0][0].data).cards
  expect(cards[0]._study.id).toMatch(/^card_/)
  expect(cards[0]._study.id).not.toBe(cards[1]._study.id)
})
it('read-only previews never mutate card identity or expose editing', () => {
  const change = vi.fn()
  render(<NoteFlashcards data={JSON.stringify({ cards: [{ front: 'Q', back: 'A' }] })} readOnly onChange={change}/>)
  expect(change).not.toHaveBeenCalled()
  expect(screen.queryByRole('button', { name: '编辑' })).not.toBeInTheDocument()
})
