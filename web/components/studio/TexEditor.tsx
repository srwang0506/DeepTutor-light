'use client'
import { useEffect, useLayoutEffect, useRef } from 'react'
import { EditorView, basicSetup } from 'codemirror'
import { EditorState } from '@codemirror/state'
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
          EditorView.updateListener.of(update => {
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
    if (editor && editor.state.doc.toString() !== value) {
      editor.dispatch({ changes: { from: 0, to: editor.state.doc.length, insert: value } })
    }
  }, [value])
  useEffect(() => {
    if (!view.current || !jump) return
    const line = view.current.state.doc.line(
      Math.max(1, Math.min(view.current.state.doc.lines, jump.line))
    )
    view.current.dispatch({
      selection: { anchor: line.from },
      effects: EditorView.scrollIntoView(line.from, { y: 'center' }),
    })
    view.current.focus()
  }, [jump, identity])
  return <div ref={parent} className="studio-code" aria-label="LaTeX 源文件编辑器" />
}
