import { browserStorage } from '@/shared/storage'

/** Drafts belong to the authenticated account as well as the project. */
export function manuscriptDraftPrefix(principal: string, workspace: string) {
  return `studio-draft:v2:${encodeURIComponent(principal)}:${encodeURIComponent(workspace)}:`
}

export function manuscriptDraftKey(principal: string, workspace: string, document: string, path: string) {
  return `${manuscriptDraftPrefix(principal, workspace)}${encodeURIComponent(document)}:${encodeURIComponent(path)}`
}

export function migrateLocalManuscriptDrafts(principal: string, workspace: string) {
  // Old keys have no account identity. Only the local single-user account can
  // recover them; never attribute an unowned draft to a signed-in account.
  if (principal === 'single-user') {
    const legacyPrefix = `studio-draft:${workspace}:`
    for (const [key, value] of browserStorage.listRaw('session', legacyPrefix)) {
      const [document, ...path] = key.slice(legacyPrefix.length).split(':')
      const next = manuscriptDraftKey(principal, workspace, document, path.join(':'))
      const current = browserStorage.readRaw('session', next)
      const retained = `studio-legacy-draft-retained:${encodeURIComponent(key)}`
      if (current !== null) {
        if (current === value) browserStorage.removeRaw('session', key)
        // Keep a different legacy body intact, but never resurrect it after
        // the newer, account-owned draft has been saved and removed.
        else browserStorage.writeRaw('session', retained, value)
        continue
      }
      if (browserStorage.readRaw('session', retained) === value) continue
      if (browserStorage.writeRaw('session', next, value)) browserStorage.removeRaw('session', key)
    }
  }
}

export function manuscriptDraftEntries(principal: string, workspace: string) {
  const prefix = manuscriptDraftPrefix(principal, workspace)
  return browserStorage.listRaw('session', prefix).flatMap(([key, value]) => {
    try {
      const [document, path] = key.slice(prefix.length).split(':')
      if (!document || !path) return []
      return [{ key, document: decodeURIComponent(document), path: decodeURIComponent(path), value }]
    } catch { return [] }
  })
}

export function readManuscriptDraft(principal: string, workspace: string, document: string, path: string) {
  migrateLocalManuscriptDrafts(principal, workspace)
  return manuscriptDraftEntries(principal, workspace)
    .find(entry => entry.document === document && entry.path === path)?.value ?? null
}

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
