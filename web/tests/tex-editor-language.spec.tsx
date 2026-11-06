import { useState } from 'react'
import { act, render, screen } from '@testing-library/react'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { EditorView } from 'codemirror'
import { openSearchPanel, SearchQuery, setSearchQuery } from '@codemirror/search'
import { undo, undoDepth } from '@codemirror/commands'
import { expect, it, vi } from 'vitest'
import TexEditor from '@/components/studio/TexEditor'

it('updates an open editor search panel without resetting edits, query or undo history', async () => {
  const i18n = createInstance()
  await i18n.init({
    lng: 'en', fallbackLng: 'en',
    resources: {
      en: { translation: {} },
      zh: { translation: { Find: '查找', Replace: '替换', Next: '下一个', 'Replace all': '全部替换' } },
    },
  })
  const changed = vi.fn()
  function EditorExample() {
    const [text, setText] = useState('alpha\nbeta')
    return <TexEditor identity="source.tex" value={text} onSelection={() => undefined}
      onChange={(next) => { changed(next); setText(next) }} />
  }
  const rendered = render(<I18nextProvider i18n={i18n}><EditorExample /></I18nextProvider>)
  const dom = rendered.container.querySelector<HTMLElement>('.cm-editor')!
  const editor = EditorView.findFromDOM(dom)!
  act(() => {
    editor.dispatch({ changes: { from: 0, to: 5, insert: 'gamma' } })
    openSearchPanel(editor)
    editor.dispatch({ effects: setSearchQuery.of(new SearchQuery({ search: 'gamma' })) })
  })
  expect(screen.getByRole('textbox', { name: 'Find' })).toHaveValue('gamma')
  expect(undoDepth(editor.state)).toBe(1)
  await act(async () => { await i18n.changeLanguage('zh') })
  expect(EditorView.findFromDOM(dom)).toBe(editor)
  expect(editor.state.doc.toString()).toBe('gamma\nbeta')
  expect(undoDepth(editor.state)).toBe(1)
  expect(changed).toHaveBeenCalledTimes(1)
  expect(screen.getByRole('textbox', { name: '查找' })).toHaveValue('gamma')
  expect(screen.getByRole('button', { name: '全部替换' })).toBeVisible()
  act(() => { expect(undo(editor)).toBe(true) })
  expect(editor.state.doc.toString()).toBe('alpha\nbeta')
})
