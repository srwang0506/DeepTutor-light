import { apiFetch, apiUrl } from '@/lib/api'
import { scopedUrl } from '@/lib/workspace-scope'
import {
  EMPTY_STUDY_SOURCES,
  type StudyPreferences,
  type StudySnapshot,
  type NoteReviewCard,
} from './personal-study'
import type { LearningSources } from './learning-dashboard'
import type { SessionSummary } from './session-api'
import type { PracticeQuestion, PracticeSummary } from './practice-api'

export class StudyRequestError extends Error {
  constructor(public status: number) {
    super(`Study request failed: ${status}`)
  }
}
const timeQuery = (timezone: string) => new URLSearchParams({ timezone }).toString()
async function read<T>(
  path: string,
  workspace: string,
  signal?: AbortSignal,
  init?: RequestInit
): Promise<T> {
  const response = await apiFetch(apiUrl(scopedUrl(path, workspace)), {
    cache: 'no-store',
    signal,
    ...init,
  })
  if (!response.ok) throw new StudyRequestError(response.status)
  return response.json() as Promise<T>
}
export async function loadStudySnapshot(
  workspace: string,
  timezone: string,
  signal?: AbortSignal,
  chatAllowed = true,
  notesAllowed = true
): Promise<StudySnapshot> {
  const query = timeQuery(timezone)
  const [index, sessions, summary, queue, preferences, cards] = await Promise.allSettled([
    read<{ sources: LearningSources; failed: string[] }>(
      '/api/dashboard/learning-index',
      workspace,
      signal
    ),
    chatAllowed
      ? read<{ sessions: SessionSummary[] }>('/api/sessions?limit=60', workspace, signal)
      : Promise.resolve({ sessions: [] }),
    chatAllowed
      ? read<PracticeSummary>(`/api/question-notebook/practice/summary?${query}`, workspace, signal)
      : Promise.resolve(null),
    chatAllowed
      ? read<PracticeQuestion[]>(
          `/api/question-notebook/practice/queue?${query}&limit=50`,
          workspace,
          signal
        )
      : Promise.resolve([]),
    read<StudyPreferences>(`/api/dashboard/study-preferences?${query}`, workspace, signal),
    notesAllowed
      ? read<{ cards: NoteReviewCard[] }>('/api/documents/review/due', workspace, signal)
      : Promise.resolve({ cards: [] }),
  ])
  const failed: string[] = []
  for (const [name, result] of [
    ['learning', index],
    ['sessions', sessions],
    ['practice', summary],
    ['practice', queue],
    ['preferences', preferences],
    ['cards', cards],
  ] as const) {
    if (result.status === 'rejected') failed.push(name)
  }
  if (index.status === 'fulfilled') failed.push(...index.value.failed)
  return {
    cards: cards.status === 'fulfilled' ? cards.value.cards : [],
    sources: index.status === 'fulfilled' ? index.value.sources : EMPTY_STUDY_SOURCES,
    sessions: sessions.status === 'fulfilled' ? sessions.value.sessions : [],
    summary: summary.status === 'fulfilled' ? summary.value : null,
    queue: queue.status === 'fulfilled' ? queue.value : [],
    preferences: preferences.status === 'fulfilled' ? preferences.value : null,
    failed: [...new Set(failed)],
  }
}
export function updateStudyPreferences(
  workspace: string,
  timezone: string,
  patch: {
    expected_revision: number
    minutes?: number
    deferral?: { key: string; action: 'tomorrow' | 'restore' }
  }
) {
  return read<StudyPreferences>(
    `/api/dashboard/study-preferences?${timeQuery(timezone)}`,
    workspace,
    AbortSignal.timeout(15000),
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    }
  )
}
