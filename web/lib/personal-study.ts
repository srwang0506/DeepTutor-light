import type { LearningSources } from '@/lib/learning-dashboard'
import type { PracticeQuestion, PracticeSummary } from '@/lib/practice-api'
import type { SessionSummary } from '@/lib/session-api'
import { sessionRoute } from '@/lib/mastery-session'
import {
  bookRoute,
  masteryTopicRoute,
  masterySessionRoute,
  readingCollectionRoute,
} from '@/lib/learning-routes'
import { scopedUrl } from '@/lib/workspace-scope'

export interface StudyPreferences {
  revision: number
  minutes: number
  deferred: Record<string, number>
  now: number
  day_end: number
  timezone: string
}
export interface NoteReviewCard {
  page_id: string
  page_title: string
  block_id: string
  card_id: string
  front: string
  back: string
  content_hash: string
  content_revision: number
  needs_schedule_review: boolean
  previous_content?: { front?: string; back?: string } | null
  due_at: number
}
export interface StudySnapshot {
  sources: LearningSources
  cards?: NoteReviewCard[]
  sessions: SessionSummary[]
  queue: PracticeQuestion[]
  summary: PracticeSummary | null
  preferences: StudyPreferences | null
  failed: string[]
}
export type StudyKind = 'review' | 'goal' | 'reading' | 'book' | 'conversation'
export type StudyReason =
  | 'pending_answer'
  | 'mistake_due'
  | 'recall_due'
  | 'card_due'
  | 'card_changed'
  | 'goal_review'
  | 'goal_next'
  | 'continue_reading'
  | 'continue_book'
  | 'continue_chat'
  | 'running'
export interface StudySuggestion {
  key: string
  kind: StudyKind
  title: string
  context: string
  href: string
  evidenceHref: string
  reason: StudyReason
  minutes: number
  updatedAt: number
  priority: number
  group: string
}
export const EMPTY_STUDY_SOURCES: LearningSources = {
  books: [],
  mastery: [],
  reading: [],
  watching: [],
  practice: [],
}

/** Presentation-only selection. Existing stores own evidence and review scheduling. */
export function buildStudyPlan(snapshot: StudySnapshot, workspaceId: string, now: number) {
  const suggestions: StudySuggestion[] = []
  const scope = (row: { content_workspace_id?: string }) =>
    (row.content_workspace_id ?? workspaceId) === workspaceId
  const key = (kind: string, id: string | number) => `${kind}:${encodeURIComponent(String(id))}`
  const sessions = snapshot.sessions
    .filter(s => scope(s) && !s.preferences?.archived && s.message_count > 0)
    .sort((a, b) => b.updated_at - a.updated_at)
  const sessionFor = (match: (s: SessionSummary) => boolean) => sessions.find(match)
  const covered = new Set<string>()
  for (const q of snapshot.queue.filter(scope)) {
    const href = scopedUrl(`/learning/practice?question=${q.entry.id}`, workspaceId)
    suggestions.push({
      key: key('practice', q.entry.id),
      kind: 'review',
      title: q.entry.question,
      context: '',
      href,
      evidenceHref: href,
      reason: q.state.is_mistake ? 'mistake_due' : 'recall_due',
      minutes: 5,
      updatedAt: q.entry.updated_at || q.entry.created_at,
      priority: 80,
      group: 'practice',
    })
  }
  for (const card of snapshot.cards ?? []) {
    suggestions.push({
      key: key('card', `${card.page_id}:${card.card_id}`),
      kind: 'review',
      title: card.front,
      context: card.page_title,
      href: scopedUrl(
        `/co-writer?review=1&card=${encodeURIComponent(card.card_id)}&page=${card.page_id}`,
        workspaceId
      ),
      evidenceHref: scopedUrl(`/co-writer/${card.page_id}`, workspaceId) + `#${card.block_id}`,
      reason: card.needs_schedule_review ? 'card_changed' : 'card_due',
      minutes: 3,
      updatedAt: card.due_at,
      priority: card.needs_schedule_review ? 86 : 79,
      group: 'cards',
    })
  }
  for (const topic of snapshot.sources.mastery.filter(scope)) {
    if (topic.metadata.status !== 'active' || topic.next.action === 'complete') continue
    const next = topic.next
    // A user-deferred objective must not reappear as today's recommendation.
    const kp = topic.map.modules
      .flatMap(m => m.knowledge_points)
      .find(p => p.id === next.knowledge_point_id)
    if (kp?.deferred) continue
    const session = sessionFor(
      s =>
        s.preferences?.workspace_mode === 'mastery_path' &&
        s.preferences.mastery_path_id === topic.path_id
    )
    if (session) covered.add(session.session_id)
    const pending = next.action === 'answer_pending'
    const review = next.action === 'review'
    const href =
      pending && next.session_id
        ? masterySessionRoute(topic.path_id, next.session_id, workspaceId)
        : session && !review
          ? sessionRoute(session)
          : masteryTopicRoute(topic.path_id, workspaceId)
    suggestions.push({
      key: key('goal', topic.path_id),
      kind: review ? 'review' : 'goal',
      title: next.knowledge_point_name || topic.name || topic.metadata.goal,
      context: topic.name || topic.metadata.goal,
      href: scopedUrl(href, workspaceId),
      evidenceHref: scopedUrl(masteryTopicRoute(topic.path_id, workspaceId), workspaceId),
      reason: pending ? 'pending_answer' : review ? 'goal_review' : 'goal_next',
      minutes: pending || review ? 10 : 15,
      updatedAt: topic.updated_at,
      priority: pending ? 100 : review ? 85 : 60,
      group: `goal:${topic.path_id}`,
    })
  }
  for (const collection of snapshot.sources.reading.filter(scope)) {
    if (!collection.tabs.some(tab => tab.material.status === 'ready')) continue
    const session = sessionFor(
      s =>
        s.preferences?.workspace_mode === 'immersive_reading' &&
        s.preferences.reading_workspace_id === collection.workspace_id
    )
    if (session) covered.add(session.session_id)
    const href = scopedUrl(
      readingCollectionRoute(collection.workspace_id, workspaceId),
      workspaceId
    )
    suggestions.push({
      key: key('reading', collection.workspace_id),
      kind: 'reading',
      title: collection.title,
      context:
        collection.tabs.find(t => t.material.material_id === collection.active_material_id)
          ?.material.title || '',
      href: session ? sessionRoute(session) : href,
      evidenceHref: href,
      reason: 'continue_reading',
      minutes: 15,
      updatedAt: Math.max(collection.updated_at, session?.updated_at ?? 0),
      priority: 45,
      group: `reading:${collection.workspace_id}`,
    })
  }
  for (const book of snapshot.sources.books.filter(scope)) {
    if (book.status !== 'ready') continue
    const session = sessionFor(s => s.preferences?.book_id === book.id)
    if (session) covered.add(session.session_id)
    const href = scopedUrl(bookRoute(book.id, null, workspaceId), workspaceId)
    suggestions.push({
      key: key('book', book.id),
      kind: 'book',
      title: book.title,
      context: '',
      href: session ? sessionRoute(session) : href,
      evidenceHref: href,
      reason: 'continue_book',
      minutes: 15,
      updatedAt: Math.max(book.updated_at, session?.updated_at ?? 0),
      priority: 40,
      group: `book:${book.id}`,
    })
  }
  for (const s of sessions.slice(0, 8)) {
    if (covered.has(s.session_id)) continue
    // A hidden/archived learning record must not sneak back in as a loose chat.
    if (
      ['mastery_path', 'immersive_reading'].includes(s.preferences?.workspace_mode ?? '') ||
      s.preferences?.book_id
    )
      continue
    const href = sessionRoute(s)
    suggestions.push({
      key: key('chat', s.session_id),
      kind: 'conversation',
      title: s.title,
      context: '',
      href,
      evidenceHref: href,
      reason: s.status === 'running' ? 'running' : 'continue_chat',
      minutes: 10,
      updatedAt: s.updated_at,
      priority: s.status === 'running' ? 95 : 50,
      group: 'conversations',
    })
  }
  const unique = [...new Map(suggestions.map(item => [item.key, item])).values()].sort(
    (a, b) => b.priority - a.priority || b.updatedAt - a.updatedAt || a.key.localeCompare(b.key)
  )
  const deferred = unique.filter(item => (snapshot.preferences?.deferred[item.key] ?? 0) > now)
  const available = unique.filter(item => !deferred.includes(item))
  const budget = snapshot.preferences?.minutes ?? 30
  const selected: StudySuggestion[] = [],
    later: StudySuggestion[] = []
  const groups = new Map<string, number>()
  let minutes = 0
  for (const item of available) {
    const maxInGroup = ['practice', 'cards'].includes(item.group) ? 3 : 1
    if (
      selected.length < 5 &&
      minutes + item.minutes <= budget &&
      (groups.get(item.group) ?? 0) < maxInGroup
    ) {
      selected.push(item)
      minutes += item.minutes
      groups.set(item.group, (groups.get(item.group) ?? 0) + 1)
    } else later.push(item)
  }
  return { selected, later, deferred, minutes, budget }
}
