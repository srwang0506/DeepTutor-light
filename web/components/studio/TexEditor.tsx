'use client'
import { useTranslation } from 'react-i18next'
import { useEffect, useLayoutEffect, useMemo, useRef } from 'react'
import { EditorView, basicSetup } from 'codemirror'
import { Compartment, EditorState } from '@codemirror/state'
import { closeSearchPanel, getSearchQuery, openSearchPanel, searchPanelOpen, setSearchQuery } from '@codemirror/search'
import { StreamLanguage } from '@codemirror/language'
import { stex } from '@codemirror/legacy-modes/mode/stex'
export interface TextSelection {
  start: number
  end: number
  text: string
}
export default function TexEditor({
  identity,
  value,
  onChange,
  onSelection,
  jump,
}: {
  identity: string
  value: string
  onChange: (text: string) => void
  onSelection: (selection: TextSelection) => void
  jump?: { line: number; nonce: number }
}) {
  const { t } = useTranslation()
  const phrases = useMemo(() => ({
    Find: t('Find'), Replace: t('Replace'), next: t('Next'), previous: t('Previous'),
    all: t('All'), 'match case': t('Match case'), regexp: t('Regular expression'),
    'by word': t('Whole words'), replace: t('Replace'), 'replace all': t('Replace all'),
    close: t('Close'), 'Go to line': t('Go to line'), go: t('Go'),
    'current match': t('Current match'), 'on line': t('On line'),
    'replaced match on line $': t('Replaced match on line $'),
    'replaced $ matches': t('Replaced $ matches'),
    'Fold line': t('Fold line'), 'Unfold line': t('Unfold line'),
    'Folded lines': t('Folded lines'), 'Unfolded lines': t('Unfolded lines'),
    to: t('To'), 'folded code': t('Folded code'), unfold: t('Unfold'),
    Completions: t('Completions'), 'Selection deleted': t('Selection deleted'),
    'Control character': t('Control character'),
  }), [t])
  const language = useRef(new Compartment())
  const initialPhrases = useRef(phrases)
  useLayoutEffect(() => { initialPhrases.current = phrases }, [phrases])

  const parent = useRef<HTMLDivElement>(null),
    view = useRef<EditorView | null>(null),
    callbacks = useRef({ onChange, onSelection }),
    initial = useRef(value)
  useLayoutEffect(() => {
    callbacks.current = { onChange, onSelection }
    initial.current = value
  }, [onChange, onSelection, value])
  useEffect(() => {
    if (!parent.current) return
    const editor = new EditorView({
      parent: parent.current,
      state: EditorState.create({
        doc: initial.current,
        extensions: [
          basicSetup,
          language.current.of(EditorState.phrases.of(initialPhrases.current)),
          StreamLanguage.define(stex),
          EditorView.lineWrapping,
          EditorView.theme({
            '&': { backgroundColor: 'var(--background)', color: 'var(--foreground)' },
            '.cm-gutters': {
              backgroundColor: 'var(--background)',
              color: 'var(--muted-foreground)',
              borderRight: '1px solid var(--border)',
            },
            '.cm-activeLine': { backgroundColor: '#7a9a8210' },
            '.cm-activeLineGutter': { backgroundColor: '#7a9a8220' },
            '.cm-cursor': { borderLeftColor: 'var(--foreground)' },
            '.cm-content': { padding: '16px 0' },
            '.cm-line': { padding: '0 18px' },
          }),
          EditorView.updateListener.of((update) => {
            if (update.docChanged) callbacks.current.onChange(update.state.doc.toString())
            if (update.selectionSet || update.docChanged) {
              const selection = update.state.selection.main
              const text = update.state.doc.toString()
              callbacks.current.onSelection({
                start: Array.from(text.slice(0, selection.from)).length,
                end: Array.from(text.slice(0, selection.to)).length,
                text: text.slice(selection.from, selection.to),
              })
            }
          }),
        ],
      }),
    })
    view.current = editor
    return () => {
      editor.destroy()
      view.current = null
    }
  }, [identity])
  useEffect(() => {
    const editor = view.current
    if (!editor) return
    // Reconfigure the existing state so language changes retain document history.
    // CodeMirror's built-in search panel captures labels only when it opens.
    const reopenSearch = searchPanelOpen(editor.state)
    const query = reopenSearch ? getSearchQuery(editor.state) : null
    const focused = document.activeElement as HTMLInputElement | null
    const field = focused && editor.dom.contains(focused) ? focused.name : ''
    const range = focused && (field === 'search' || field === 'replace')
      ? [focused.selectionStart, focused.selectionEnd] : null
    if (reopenSearch) closeSearchPanel(editor)
    editor.dispatch({ effects: language.current.reconfigure(EditorState.phrases.of(phrases)) })
    if (reopenSearch) {
      openSearchPanel(editor)
      if (query) editor.dispatch({ effects: setSearchQuery.of(query) })
      const input = Array.from(editor.dom.querySelectorAll<HTMLInputElement>('.cm-search input'))
        .find((element) => element.name === field)
      if (input) {
        input.focus()
        if (range) input.setSelectionRange(range[0], range[1])
      }
    }
  }, [phrases, identity])
  useEffect(() => {
    const editor = view.current
    if (editor && editor.state.doc.toString() !== value) {
      editor.dispatch({ changes: { from: 0, to: editor.state.doc.length, insert: value } })
    }
  }, [value])
  useEffect(() => {
    if (!view.current || !jump) return
    const line = view.current.state.doc.line(
      Math.max(1, Math.min(view.current.state.doc.lines, jump.line)),
    )
    view.current.dispatch({
      selection: { anchor: line.from },
      effects: EditorView.scrollIntoView(line.from, { y: 'center' }),
    })
    view.current.focus()
  }, [jump, identity])
  return <div ref={parent} className="studio-code" aria-label={t('LaTeX 源文件编辑器')} />
}
