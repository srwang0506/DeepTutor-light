import { describe, expect, it } from 'vitest'
import { buildStudyPlan, EMPTY_STUDY_SOURCES, type StudySnapshot } from '@/lib/personal-study'
import type { PracticeQuestion } from '@/lib/practice-api'
import type { MasteryTopic } from '@/lib/learning-api'
import type { SessionSummary } from '@/lib/session-api'

const state = (extra: Partial<StudySnapshot> = {}): StudySnapshot => ({
  sources: EMPTY_STUDY_SOURCES,
  sessions: [],
  queue: [],
  summary: null,
  preferences: {
    revision: 0,
    minutes: 30,
    deferred: {},
    now: 1000,
    day_end: 2000,
    timezone: 'UTC',
  },
  failed: [],
  ...extra,
})
const question = (id: number, workspace = 'a') =>
  ({
    content_workspace_id: workspace,
    entry: { id, question: `Question ${id}`, created_at: id, updated_at: id },
    state: { is_mistake: true },
  }) as PracticeQuestion
const session = (id: string, extra = {}) =>
  ({
    id,
    session_id: id,
    title: id,
    content_workspace_id: 'a',
    created_at: 1,
    updated_at: 2,
    message_count: 2,
    last_message: '',
    ...extra,
  }) as SessionSummary
const goal = (extra = {}) =>
  ({
    path_id: 'g',
    name: 'Algorithms',
    content_workspace_id: 'a',
    updated_at: 2,
    metadata: { status: 'active' },
    map: { modules: [] },
    next: {
      action: 'answer_pending',
      knowledge_point_id: 'k',
      knowledge_point_name: 'BFS',
      session_id: 'original',
    },
    ...extra,
  }) as unknown as MasteryTopic

describe('personal study selection', () => {
  it('prioritizes a pending answer, stays in budget, and limits review flooding', () => {
    const snapshot = state({
      sources: { ...EMPTY_STUDY_SOURCES, mastery: [goal()] },
      queue: Array.from({ length: 20 }, (_, i) => question(i)),
    })
    const before = JSON.stringify(snapshot)
    const plan = buildStudyPlan(snapshot, 'a', 1000)
    expect(plan.selected[0].reason).toBe('pending_answer')
    expect(plan.selected[0].href).toContain('/learning/mastery/g/sessions/original')
    expect(plan.minutes).toBeLessThanOrEqual(30)
    expect(plan.selected.filter(s => s.group === 'practice')).toHaveLength(3)
    expect(JSON.stringify(snapshot)).toBe(before)
  })
  it('excludes other projects, archived and empty chats', () => {
    const plan = buildStudyPlan(
      state({
        queue: [question(1, 'b')],
        sessions: [
          session('valid'),
          session('other', { content_workspace_id: 'b' }),
          session('archived', { preferences: { archived: true } }),
          session('empty', { message_count: 0 }),
        ],
      }),
      'a',
      1000
    )
    expect(plan.selected.map(s => s.title)).toEqual(['valid'])
    expect(plan.selected[0].href).toContain('dt_workspace=a')
  })
  it('defers without changing evidence and returns the item the next day', () => {
    const snapshot = state({ queue: [question(1)] })
    snapshot.preferences!.deferred = { 'practice:1': 2000 }
    expect(buildStudyPlan(snapshot, 'a', 1000).selected).toHaveLength(0)
    expect(buildStudyPlan(snapshot, 'a', 1000).deferred).toHaveLength(1)
    expect(buildStudyPlan(snapshot, 'a', 2001).selected).toHaveLength(1)
    expect(snapshot.queue[0].state.is_mistake).toBe(true)
  })
  it('does not bring archived or deferred goals back via their conversations', () => {
    const sources = {
      ...EMPTY_STUDY_SOURCES,
      mastery: [
        goal({ metadata: { status: 'archived' } }),
        goal({
          path_id: 'deferred',
          map: { modules: [{ knowledge_points: [{ id: 'k', deferred: true }] }] },
        }),
      ],
    }
    const sessions = [
      session('old', { preferences: { workspace_mode: 'mastery_path', mastery_path_id: 'g' } }),
    ]
    expect(buildStudyPlan(state({ sources, sessions }), 'a', 1000).selected).toHaveLength(0)
  })
  it('opens the original reader conversation once with its context intact', () => {
    const source = {
      workspace_id: 'folder',
      title: 'Lecture',
      content_workspace_id: 'a',
      updated_at: 1,
      tabs: [{ material: { status: 'ready' } }],
    }
    const prefs = { workspace_mode: 'immersive_reading', reading_workspace_id: 'folder' }
    const snapshot = state({
      sources: { ...EMPTY_STUDY_SOURCES, reading: [source] as StudySnapshot['sources']['reading'] },
      sessions: [
        session('s1', { preferences: prefs }),
        session('s2', { preferences: prefs, updated_at: 5 }),
      ],
    })
    const plan = buildStudyPlan(snapshot, 'a', 1000)
    expect(plan.selected).toHaveLength(1)
    expect(plan.selected[0].href).toBe('/learning/reading/folder/sessions/s2?dt_workspace=a')
  })
  it('keeps a partial source outage explicit and never invents activity', () => {
    const snapshot = state({ failed: ['practice'], summary: null })
    expect(buildStudyPlan(snapshot, 'a', 1000).selected).toEqual([])
    expect(snapshot.summary).toBeNull()
  })
})

it('adds note review and changed-card decisions without rescheduling their source', () => {
  const snapshot = state({ cards: [{ page_id: 'p', page_title: 'Units', block_id: 'b', card_id: 'b:123', front: 'mW or MW?', back: 'Different units', due_at: 2000, content_revision: 2, content_hash: 'a'.repeat(64), needs_schedule_review: true }] })
  const before = JSON.stringify(snapshot)
  const plan = buildStudyPlan(snapshot, 'a', 1000)
  expect(plan.selected[0].reason).toBe('card_changed')
  expect(plan.selected[0].href).toContain('card=b%3A123')
  expect(plan.selected[0].href).toContain('dt_workspace=a')
  expect(plan.selected[0].evidenceHref).toBe('/co-writer/p?dt_workspace=a#b')
  expect(JSON.stringify(snapshot)).toBe(before)
})
