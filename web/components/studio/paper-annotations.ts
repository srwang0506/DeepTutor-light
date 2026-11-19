import { EditorState, StateField } from '@codemirror/state'
import { Decoration, EditorView, type DecorationSet } from '@codemirror/view'
import { mapCommentEdit } from '@/lib/latexcoder/comment-edits'
import { parseArchivedComments, parseReviews, reviewFormat } from '@/lib/latexcoder/review'

const hasAnnotations =
  /\\(?:cmtbg|revbg|addbg|delbg)\{|latexcoder:(?:thread|cmtbg|revbg|addbg|delbg):/

function annotationView(source: string) {
  if (!hasAnnotations.test(source)) return { marks: Decoration.none, hidden: Decoration.none }
  const hidden = [],
    marks = []
  for (const item of parseReviews(source)) {
    hidden.push(
      Decoration.replace({}).range(item.from, item.bodyFrom),
      Decoration.replace({}).range(item.bodyTo, item.to)
    )
    if (item.bodyFrom < item.bodyTo) {
      marks.push(
        Decoration.mark({ class: 'writer-inline-' + item.kind }).range(item.bodyFrom, item.bodyTo)
      )
    }
  }
  for (const item of parseArchivedComments(source)) {
    hidden.push(Decoration.replace({}).range(item.from, item.to))
  }
  return {
    marks: Decoration.set([...hidden, ...marks], true),
    hidden: Decoration.set(hidden, true),
  }
}

const annotationField = StateField.define<{ marks: DecorationSet; hidden: DecorationSet }>({
  create: state => annotationView(state.doc.toString()),
  update: (value, transaction) =>
    transaction.docChanged ? annotationView(transaction.newDoc.toString()) : value,
  provide: field => [
    EditorView.decorations.from(field, value => value.marks),
    EditorView.atomicRanges.of(view => view.state.field(field).hidden),
  ],
})

export function paperAnnotations(path: string) {
  return [
    annotationField,
    EditorState.transactionFilter.of(transaction => {
      if (
        !transaction.docChanged ||
        (!transaction.isUserEvent('input') && !transaction.isUserEvent('delete'))
      )
        return transaction
      const source = transaction.startState.doc.toString()
      if (!hasAnnotations.test(source)) return transaction
      const mapped = mapCommentEdit(transaction, parseReviews(source), {
        format: reviewFormat(path),
        author: 'Author',
      })
      return mapped
        ? [
            transaction,
            { changes: mapped.correction, selection: mapped.selection, sequential: true },
          ]
        : transaction
    }),
  ]
}
