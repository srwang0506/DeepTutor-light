import { EditorState } from '@codemirror/state'
import { EditorView, hoverTooltip, keymap } from '@codemirror/view'
import type { CompletionContext } from '@codemirror/autocomplete'
import katex from 'katex'
import { citationEntries } from '@/lib/latexcoder/bibliography'
import { labeledGraphics, normalizeProjectPath, resolveGraphicsPath } from '@/lib/latexcoder/assets'
import {
  referenceDefinition,
  referenceLinks,
  withoutComments,
  type ReferenceLink,
} from '@/lib/latexcoder/references'
import { mathRegions, renderableMath } from '@/lib/latexcoder/math'
import { expandLatexMacros, parseLatexMacros } from '@/lib/latexcoder/latex-macros'

export type PaperFile = { path: string; hash: string; size: number; editable: boolean }
export type PaperIndex = {
  sources: Record<string, string>
  files: PaperFile[]
  omitted: string[]
  main: string
}
export type PaperNavigation = {
  index: PaperIndex
  path: string
  asset: (path: string) => string
  open: (path: string, line?: number) => void
}

export function editorNavigation(current: () => PaperNavigation | undefined) {
  function locate(link: ReferenceLink, nav: PaperNavigation, source: string) {
    const sources = { ...nav.index.sources, [nav.path]: source }
    if (link.kind === 'url') {
      try {
        const url = new URL(link.key)
        if (['http:', 'https:'].includes(url.protocol)) return { url: url.href }
      } catch {
        /* Invalid author input is not a link. */
      }
    }
    if (link.kind === 'asset') {
      const file = resolveGraphicsPath(link.key, nav.path, nav.index.files)
      if (file) return { path: file }
    }
    if (link.kind === 'file') {
      const dir = nav.path.split('/').slice(0, -1).join('/')
      for (const name of [link.key, link.key + '.tex']) {
        for (const candidate of [
          normalizeProjectPath(dir + '/' + name),
          normalizeProjectPath(name),
        ]) {
          if (nav.index.files.some(f => f.path === candidate)) return { path: candidate }
        }
      }
    }
    if (link.kind === 'label' || link.kind === 'cite') {
      for (const [path, text] of Object.entries(sources)) {
        if (link.kind === 'cite' && !/\.bib$/i.test(path)) continue
        const def = referenceDefinition(text, link.key, link.kind)
        if (def) return { path, line: text.slice(0, def.from).split('\n').length }
      }
    }
  }
  function target(view: EditorView, pos: number) {
    const nav = current()
    if (!nav) return false
    const source = view.state.doc.toString()
    const link = referenceLinks(source).find(item => item.from <= pos && pos <= item.to)
    if (!link) return false
    const found = locate(link, nav, source)
    if (found?.url) window.open(found.url, '_blank', 'noopener,noreferrer')
    else if (found?.path) nav.open(found.path, found.line)
    else return false
    return true
  }
  function complete(context: CompletionContext) {
    const nav = current()
    const match = context.matchBefore(
      /\\(?:cite[pt]?|ref|eqref|cref|autoref)\*?(?:\[[^\]\n]*\])*\{[^}\n]*$/
    )
    if (!nav || !match) return null
    const options = new Map<string, { label: string; detail?: string; info?: string }>()
    const sources = { ...nav.index.sources, [nav.path]: context.state.doc.toString() }
    for (const [path, source] of Object.entries(sources)) {
      if (match.text.startsWith('\\cite') && /\.bib$/i.test(path)) {
        for (const entry of citationEntries(source)) {
          options.set(entry.key, {
            label: entry.key,
            detail: entry.title,
            info: [entry.authors.join(', '), entry.year].filter(Boolean).join(' · '),
          })
        }
      } else if (!match.text.startsWith('\\cite') && /\.tex$/i.test(path)) {
        for (const label of withoutComments(source).matchAll(/\\label\s*\{([^}]+)\}/g))
          options.set(label[1], { label: label[1], detail: path })
      }
    }
    const prefix = match.text.match(/[^,\s{]*$/)?.[0] || ''
    return {
      from: context.pos - prefix.length,
      options: [...options.values()],
      validFor: /^[^,\s{}]*$/,
    }
  }
  return [
    EditorState.languageData.of(() => [{ autocomplete: complete }]),
    keymap.of([{ key: 'F12', run: view => target(view, view.state.selection.main.head) }]),
    EditorView.domEventHandlers({
      mousedown(event, view) {
        if (!(event.metaKey || event.ctrlKey)) return false
        const pos = view.posAtCoords({ x: event.clientX, y: event.clientY })
        if (pos == null || !target(view, pos)) return false
        event.preventDefault()
        return true
      },
    }),
    hoverTooltip(
      (view, pos) => {
        const nav = current()
        if (!nav) return null
        const source = view.state.doc.toString()
        const link = referenceLinks(source).find(item => item.from <= pos && pos < item.to)
        const sources = { ...nav.index.sources, [nav.path]: source }
        let equation = mathRegions(source).find(item => item.from <= pos && pos < item.to)
        let image: string | null = null
        let caption = ''
        if (link) {
          const definition = locate(link, nav, source)
          caption = definition?.path || definition?.url || link.key
          if (link.kind === 'asset') image = definition?.path || null
          if (link.kind === 'cite' && definition?.path) {
            const entry = citationEntries(sources[definition.path] || '').find(
              item => item.key === link.key
            )
            if (entry)
              caption = [entry.title, entry.authors.join(', '), entry.year]
                .filter(Boolean)
                .join(' · ')
          }
          if (link.kind === 'label' && definition?.path) {
            const body = sources[definition.path] || ''
            equation = mathRegions(body).find(item => item.labels.includes(link.key))
            const graphic = labeledGraphics(body).find(item => item.label === link.key)
            if (graphic)
              image = resolveGraphicsPath(graphic.asset, definition.path, nav.index.files)
          }
        }
        if (!equation && !link) return null
        return {
          pos: link?.from ?? equation!.from,
          end: link?.to ?? equation?.to,
          above: true,
          create() {
            const dom = document.createElement('div')
            dom.className = 'writer-hover'
            if (equation) {
              try {
                const expanded = expandLatexMacros(
                  renderableMath(equation),
                  parseLatexMacros(Object.values(sources))
                )
                katex.render(expanded, dom, {
                  throwOnError: false,
                  trust: false,
                  maxSize: 20,
                  maxExpand: 500,
                })
              } catch {
                dom.textContent = equation.source
              }
            }
            if (image && /\.(png|jpe?g|svg)$/i.test(image)) {
              const img = document.createElement('img')
              img.src = nav.asset(image)
              img.alt = image
              dom.append(img)
            }
            if (caption) {
              const line = document.createElement('small')
              line.textContent = caption
              dom.append(line)
            }
            return { dom }
          },
        }
      },
      { hoverTime: 350 }
    ),
  ]
}
