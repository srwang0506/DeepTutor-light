import { apiFetch } from './api'
import { post, studio, studioUrl, type StudioRecord } from './studio-api'
import type { StudioSourceReference } from './studio-references'

export type PaperReviewMode = 'self' | 'full' | 'quick' | 'rebuttal' | 'revise'

export interface PaperReviewIssue {
  id: string
  title: string
  body: string
  request: string
  severity: string
  status: string
  confidence: string | number
  page: number | null
  quote: string
  location_status: 'matched' | 'missing' | 'unverified'
  tracking_status: 'open' | 'working' | 'addressed'
  response: string
}

export interface PaperReviewData {
  title: string
  mode: PaperReviewMode
  venue: string
  language: 'zh' | 'en'
  instructions: string
  skill: { name: string; version: string; commit: string; tree_hash: string }
  session_id: string | null
  target_ref: StudioSourceReference
  manuscript_id?: string
  build_id?: string
  checkpoint_hash: string | null
  issues: PaperReviewIssue[]
  checks: Record<string, string>
  pending: string[]
  status: 'draft' | 'checkpointed'
}

export type PaperReview = StudioRecord<PaperReviewData> & { review_ref: StudioSourceReference }

export type PaperReviewTarget =
  | { kind: 'edition'; reference: StudioSourceReference }
  | { kind: 'manuscript'; manuscriptId: string; buildId: string | null }

export function paperReviewOptions(workspace: string, signal?: AbortSignal) {
  return studio<{ venues: { id: string; label: string }[]; modes: PaperReviewMode[] }>(workspace, '/paper-reviews/options', {}, signal)
}

export interface CreatePaperReview {
  request_id: string
  edition_id?: string
  revision?: number
  blob_hash?: string
  manuscript_id?: string
  build_id?: string
  mode: PaperReviewMode
  venue: string
  language: 'zh' | 'en'
  instructions: string
}

export function listPaperReviews(workspace: string, target: PaperReviewTarget, signal?: AbortSignal) {
  const params = new URLSearchParams(target.kind === 'edition'
    ? { edition_id: target.reference.id }
    : { manuscript_id: target.manuscriptId })
  return studio<{ reviews: PaperReview[] }>(workspace, `/paper-reviews?${params}`, {}, signal)
}

export function getPaperReview(workspace: string, id: string, signal?: AbortSignal) {
  return studio<PaperReview>(workspace, `/paper-reviews/${encodeURIComponent(id)}`, {}, signal)
}

export function createPaperReview(workspace: string, payload: CreatePaperReview) {
  return post<PaperReview>(workspace, '/paper-reviews', payload)
}

/** The server creates and binds a real native session before its first turn. */
export function ensurePaperReviewSession(workspace: string, id: string) {
  return post<PaperReview>(workspace, `/paper-reviews/${encodeURIComponent(id)}/session`, {})
}

export function updatePaperReviewIssue(workspace: string, review: PaperReview, issue: PaperReviewIssue, status: PaperReviewIssue['tracking_status'], response: string) {
  return post<PaperReview>(workspace, `/paper-reviews/${encodeURIComponent(review.id)}/issues/${encodeURIComponent(issue.id)}`, {
    status, response, expected_revision: review.revision,
  })
}

export async function exportPaperReview(workspace: string, id: string, format: 'markdown' | 'json') {
  const response = await apiFetch(studioUrl(`/paper-reviews/${encodeURIComponent(id)}/export?format=${format}`, workspace))
  if (!response.ok) throw new Error(`Request failed: ${response.status}`)
  const blob = await response.blob()
  const href = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = href
  link.download = `paper-review-${id}.${format === 'json' ? 'json' : 'md'}`
  link.click()
  setTimeout(() => URL.revokeObjectURL(href), 1000)
}

export function reviewMatchesEdition(review: PaperReview, reference: StudioSourceReference) {
  const target = review.data.target_ref
  return target.kind === 'edition' && target.scope === reference.scope && target.id === reference.id &&
    target.revision === reference.revision && target.locator?.blob_hash === reference.locator?.blob_hash
}
