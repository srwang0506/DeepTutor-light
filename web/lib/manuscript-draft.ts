export interface FileVersion {
  path: string
  hash: string
  text: string
}
export interface DraftConflict {
  base: FileVersion
  disk: FileVersion
}
export interface SavedDraft {
  hash: string
  text: string
  baseText?: string
  conflict?: DraftConflict | null
}

/** Never attach a new disk hash to an old draft before an explicit resolution. */
export function recoverDraft(source: FileVersion, recovered: SavedDraft | null) {
  const text = recovered?.text ?? source.text
  const conflicting =
    !!recovered && (recovered.hash !== source.hash || !!recovered.conflict) && text !== source.text
  const base = conflicting
    ? { ...source, hash: recovered!.hash, text: recovered!.baseText ?? '' }
    : source
  return { base, text, conflict: conflicting ? { base, disk: source } : null }
}
export function assertDraftCanSave(conflict: DraftConflict | null) {
  if (conflict) throw new Error('请先处理版本冲突；关闭比较窗口不会恢复自动保存。')
}
