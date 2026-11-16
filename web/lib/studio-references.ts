import { recoveryPrincipal } from '@/lib/recovery-principal'
import { browserStorage } from '@/shared/storage'

export const STUDIO_SOURCE_LIMIT = 12

export interface StudioSourceReference {
  owner?: 'current_account'
  scope: string
  kind: 'edition' | 'evidence' | 'observation' | 'run' | 'remote_snapshot' | 'artifact' | 'claim' | 'paper_review'
  id: string
  revision: number
  locator?: { blob_hash?: string; page?: number; quote?: string; region?: [number, number, number, number] }
}

export interface StudioSourceItem {
  ref: StudioSourceReference
  title: string
  description: string
  href: string
  workspace_name?: string
  external?: boolean
  pages?: number
}

export function studioReferenceKey(ref: StudioSourceReference) {
  return JSON.stringify([ref.scope, ref.kind, ref.id, ref.revision, ref.locator?.blob_hash ?? '', ref.locator?.page ?? null, ref.locator?.quote ?? '', ref.locator?.region ?? null])
}

export function normalizeStudioReferences(input: unknown): StudioSourceReference[] {
  if (!Array.isArray(input)) return []
  const allowed = ['edition', 'evidence', 'observation', 'run', 'remote_snapshot', 'artifact', 'claim', 'paper_review']
  const items = input.filter((value): value is StudioSourceReference =>
    !!value && typeof value === 'object' && allowed.includes(value.kind) &&
    typeof value.scope === 'string' && typeof value.id === 'string' && value.id.length > 0 &&
    Number.isSafeInteger(value.revision) && value.revision > 0 &&
    (value.locator == null || (typeof value.locator === 'object' &&
      (value.locator.page == null || (Number.isSafeInteger(value.locator.page) && value.locator.page > 0)) &&
      (value.locator.blob_hash == null || typeof value.locator.blob_hash === 'string') &&
      (value.locator.quote == null || typeof value.locator.quote === 'string'))),
  )
  return Array.from(new Map(items.map(item => [studioReferenceKey(item), item])).values()).slice(0, STUDIO_SOURCE_LIMIT)
}

export function studioReferenceHref(ref: StudioSourceReference): string {
  const query = new URLSearchParams({ revision: String(ref.revision) })
  if (ref.kind === 'edition') {
    if (ref.locator?.blob_hash) query.set('file', ref.locator.blob_hash)
    if (ref.locator?.page) query.set('page', String(ref.locator.page))
    return `/library/${encodeURIComponent(ref.id)}?${query}`
  }
  return `/objects/${encodeURIComponent(ref.id)}?${query}`
}

const handoffKey = (workspace: string) => {
  const owner = recoveryPrincipal()
  return owner ? `deeptutor.pendingStudioSources:${JSON.stringify([owner, workspace])}` : null
}

export function handoffStudioReferences(workspace: string, items: StudioSourceItem[]) {
  const key = handoffKey(workspace)
  if (key) browserStorage.writeRaw('session', key, JSON.stringify(items.slice(0, STUDIO_SOURCE_LIMIT)))
}

export function consumeStudioReferences(workspace: string): StudioSourceItem[] {
  const key = handoffKey(workspace)
  if (!key) return []
  const raw = browserStorage.readRaw('session', key)
  if (!raw) return []
  browserStorage.removeRaw('session', key)
  try {
    const items: StudioSourceItem[] = JSON.parse(raw)
    return normalizeStudioReferences(items.map(item => item.ref)).flatMap(ref => {
      const item = items.find(candidate => studioReferenceKey(candidate.ref) === studioReferenceKey(ref))
      return item && typeof item.title === 'string' ? [item] : []
    })
  } catch {
    return []
  }
}

export interface StudioCitation { href: string; title: string; revision: number; page?: number; line?: number }

/** Render only typed, same-application source links returned by the reader tool. */
export function studioCitations(events: { metadata?: Record<string, unknown> }[]): StudioCitation[] {
  const accepted = new Map<string, StudioCitation>()
  for (const event of events) {
    const tool = event.metadata?.tool_metadata
    const nested = tool && typeof tool === 'object' ? (tool as Record<string, unknown>).studio_citation : null
    const row = (nested ?? event.metadata?.studio_citation) as Partial<StudioCitation> | undefined
    if (!row || typeof row.href !== 'string' || !/^\/(?:library|objects)\/[\w-]+(?:\?|$)/.test(row.href) ||
      typeof row.title !== 'string' || typeof row.revision !== 'number' ||
      !Number.isSafeInteger(row.revision) || row.revision < 1) continue
    accepted.set(row.href, { href: row.href, title: row.title, revision: row.revision, page: row.page, line: row.line })
  }
  return [...accepted.values()]
}
