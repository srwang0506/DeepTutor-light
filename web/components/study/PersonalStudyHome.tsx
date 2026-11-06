'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import Continuations from '@/components/studio/Continuations'
import {
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Check,
  ChevronDown,
  Clock3,
  FolderOpen,
  MessageCircle,
  RotateCcw,
  Route,
  Sunrise,
  FlaskConical,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { FeaturePage } from '@/components/layout/FeaturePage'
import { useWorkspaceChoices } from '@/components/workspaces/useWorkspaceChoices'
import { useActiveWorkspaceId } from '@/hooks/useActiveWorkspaceId'
import { useAuthStatus } from '@/hooks/useAuthStatus'
import { scopedUrl } from '@/lib/workspace-scope'
import { subscribeSessionChanges } from '@/lib/session-events'
import {
  buildStudyPlan,
  type StudySnapshot,
  type StudySuggestion,
  type StudyReason,
} from '@/lib/personal-study'
import {
  loadStudySnapshot,
  updateStudyPreferences,
  StudyRequestError,
} from '@/lib/personal-study-api'
import './personal-study.css'

const reasons: Record<StudyReason, [string, string]> = {
  pending_answer: [
    '上次还有一道题等待你的回答。回到原会话，保留当时的题目与提示。',
    'A question is still waiting for your answer. Continue in its original conversation.',
  ],
  mistake_due: [
    '这道题有错题记录，原有复习排期已到。先独立回忆，再核对反馈。',
    'A previously missed question is due in your review schedule. Try recalling before checking feedback.',
  ],
  recall_due: [
    '这道题已到复习时间。用一次新的回忆，检查现在是否还记得。',
    'This question is due for review. A fresh attempt checks what you can still recall.',
  ],
  card_due: [
    '这张笔记闪卡已到复习时间。先独立回忆，再翻面核对。',
    'A notebook card is due. Recall the answer before revealing it.',
  ],
  card_changed: [
    '这张卡片的内容有实质修改。核对前后差异，选择保留排期或重新学习。',
    'This card changed. Compare its content and choose to keep or reset its schedule.',
  ],
  goal_review: [
    '这个知识点的复习已到期。打开目标，可以核对学习记录和复习依据。',
    'This objective is due for review. Its learning record explains the schedule.',
  ],
  goal_next: [
    '这是当前学习路径给出的下一步。你可以查看目标依据，或在路径中调整安排。',
    'Your learning path recommends this next step. Review its evidence or adjust the path.',
  ],
  continue_reading: [
    '这份资料最近被打开或讨论过。继续原来的阅读与问答，不重新开始。',
    'You recently opened or discussed this material. Continue with its reading context.',
  ],
  continue_book: [
    '这本教材最近有更新或讨论。打开原内容，继续阅读和练习。',
    'This book has recent activity. Return to its content to keep reading and practicing.',
  ],
  continue_chat: [
    '这是最近有实际消息的对话。接着已有上下文继续，不代表上次的内容已掌握。',
    'A recent conversation with saved messages. Continue its context; activity alone does not establish mastery.',
  ],
  running: [
    '这段对话仍在执行。打开已有任务查看进度，不重复启动。',
    'This conversation is still running. Open the existing task to follow its progress.',
  ],
}
const icons = {
  review: RotateCcw,
  goal: Route,
  reading: BookOpen,
  book: BookOpen,
  conversation: MessageCircle,
}
const labels = {
  review: ['独立回忆', 'Recall'],
  goal: ['推进目标', 'Learning goal'],
  reading: ['继续阅读', 'Read'],
  book: ['继续教材', 'Book'],
  conversation: ['接续思考', 'Continue'],
}

export default function PersonalStudyHome() {
  const workspace = useActiveWorkspaceId()
  return <StudyHome key={workspace} workspace={workspace} />
}

function StudyHome({ workspace }: { workspace: string }) {
  const { i18n } = useTranslation()
  const zh = i18n.language.startsWith('zh')
  const { currentName } = useWorkspaceChoices()
  const auth = useAuthStatus()
  const notesAllowed = !auth.learningPolicy
  const chatAllowed = !auth.learningPolicy || auth.learningPolicy.allowedSurfaces.includes('chat')
  const readingAllowed =
    !auth.learningPolicy || auth.learningPolicy.allowedSurfaces.includes('reading')
  const [snapshot, setSnapshot] = useState<StudySnapshot | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const busy = useRef(false),
    alive = useRef(true),
    version = useRef(0)
  const request = useRef<AbortController | null>(null)
  const cancelLoad = useCallback(() => {
    version.current++
    request.current?.abort()
  }, [])
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  const load = useCallback(async () => {
    if (auth.loading || busy.current || document.visibilityState === 'hidden') return
    const current = ++version.current
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    const data = await loadStudySnapshot(
      workspace,
      timezone,
      AbortSignal.any([controller.signal, AbortSignal.timeout(20000)]),
      chatAllowed,
      notesAllowed
    )
    if (!alive.current || current !== version.current) return
    setSnapshot(data)
    setLoading(false)
  }, [workspace, timezone, chatAllowed, notesAllowed, auth.loading])
  useEffect(() => {
    alive.current = true
    void load()
    const refresh = () => {
      if (document.visibilityState !== 'hidden') void load()
    }
    const unsubscribe = subscribeSessionChanges(refresh)
    window.addEventListener('focus', refresh)
    document.addEventListener('visibilitychange', refresh)
    const timer = window.setInterval(refresh, 60000)
    return () => {
      alive.current = false
      cancelLoad()
      unsubscribe()
      window.removeEventListener('focus', refresh)
      document.removeEventListener('visibilitychange', refresh)
      window.clearInterval(timer)
    }
  }, [load, cancelLoad])
  async function save(patch: {
    minutes?: number
    deferral?: { key: string; action: 'tomorrow' | 'restore' }
  }) {
    if (!snapshot?.preferences || busy.current) return
    busy.current = true
    setSaving(true)
    setError('')
    cancelLoad()
    try {
      const preferences = await updateStudyPreferences(workspace, timezone, {
        expected_revision: snapshot.preferences.revision,
        ...patch,
      })
      if (!alive.current) return
      setSnapshot(previous => (previous ? { ...previous, preferences } : previous))
      setNotice(
        patch.deferral?.action === 'tomorrow'
          ? zh
            ? '明天再安排；原有成绩和复习记录保持不变。'
            : 'Deferred until tomorrow. Your learning and review records stay unchanged.'
          : zh
            ? '安排已保存。'
            : 'Plan saved.'
      )
    } catch (e) {
      if (alive.current)
        setError(
          e instanceof StudyRequestError && e.status === 409
            ? zh
              ? '另一个页面修改了安排，请重新加载后再调整。'
              : 'Another tab changed this plan. Reload before editing.'
            : zh
              ? '安排没有保存成功。请重试，你的学习记录仍然保留。'
              : 'Could not save your plan. Your learning records are still saved.'
        )
    } finally {
      busy.current = false
      if (alive.current) setSaving(false)
    }
  }
  const plan = snapshot
    ? buildStudyPlan(snapshot, workspace, snapshot.preferences?.now ?? Date.now() / 1000)
    : null
  const first = plan?.selected[0]
  const text = (a: string, b: string) => (zh ? a : b)
  const validGoals = snapshot?.sources.mastery.filter(t => t.metadata.status === 'active') ?? []
  const reviewed = snapshot?.summary?.reviewed_today
  return (
    <FeaturePage className="personal-study-page">
      <div className="personal-study-shell">
        <header className="personal-study-header">
          <div>
            <div className="study-eyebrow">
              <Sunrise size={16} />
              {text('个人学习与科研', 'YOUR PERSONAL STUDIO')}
            </div>
            <h1>{text('今天，继续探索。', 'A little further, every day.')}</h1>
            <p>
              {text(
                '从一份资料、一个问题开始，把好奇变成自己的理解。',
                'A paper, a question, a new perspective. Make room for understanding.'
              )}
            </p>
          </div>
          <Link href={scopedUrl('/project', workspace)} className="study-project-chip">
            <FolderOpen size={15} />
            <span>{currentName}</span>
            <ArrowUpRight size={14} />
          </Link>
        </header>
        {(error || !!snapshot?.failed.length) && (
          <div className="study-warning" role="alert">
            <span>
              {error ||
                text(
                  '部分记录暂时无法读取，以下仅展示已加载的内容。',
                  'Some records are unavailable. Only loaded information is shown below.'
                )}
            </span>
            <button
              disabled={saving}
              onClick={() => {
                setError('')
                void load()
              }}
            >
              {text('重新加载', 'Reload')}
            </button>
          </div>
        )}
        {notice && (
          <p className="study-notice" role="status">
            <Check size={14} />
            {notice}
          </p>
        )}
        {loading ? (
          <div className="study-loading" role="status">
            {text('正在找回你的学习进度…', 'Finding where you left off…')}
          </div>
        ) : (
          <>
            <section className="study-start">
              <div className="study-start-copy">
                <div className="study-eyebrow">{text('接下来这一小步', 'YOUR NEXT STEP')}</div>
                <h2>
                  {first?.title ||
                    text(
                      plan?.deferred.length ? '今天先留一点余地。' : '从一个想弄懂的问题开始。',
                      plan?.deferred.length
                        ? 'Leave some space today.'
                        : 'Start with something you want to understand.'
                    )}
                </h2>
                <p>
                  {first
                    ? reasons[first.reason][zh ? 0 : 1]
                    : text(
                        '选一份资料，或直接提出问题。目标、练习和进展会逐渐在这里接起来。',
                        'Open some material or ask a question. Your goals, practice and progress will come together here.'
                      )}
                </p>
                <div className="study-start-actions">
                  <Link
                    className="study-primary"
                    href={first?.href || scopedUrl(chatAllowed ? '/chat' : '/learning', workspace)}
                  >
                    {text(
                      first ? '接着学' : chatAllowed ? '开始提问' : '打开我的学习',
                      first ? 'Continue' : chatAllowed ? 'Ask a question' : 'Open my learning'
                    )}
                    <ArrowRight size={16} />
                  </Link>
                  {chatAllowed && (
                    <Link
                      className="study-text-link"
                      href={scopedUrl('/learning/mastery', workspace)}
                    >
                      {text('我的学习目标', 'My learning goals')}
                      <ArrowUpRight size={14} />
                    </Link>
                  )}
                </div>
              </div>
              <StudyDeskArt />
            </section>

            <section className="study-toolbox">
              {readingAllowed && (
                <Link href={scopedUrl('/learning/reading', workspace)}>
                  <BookOpen size={18} />
                  <div>
                    <strong>{text('读一份资料', 'Read your materials')}</strong>
                    <small>
                      {text('原文、批注与提问在一起', 'Sources, notes and questions together')}
                    </small>
                  </div>
                  <ArrowUpRight size={14} />
                </Link>
              )}
              {!auth.learningPolicy && (
                <Link href={scopedUrl('/research', workspace)}>
                  <FlaskConical size={18} />
                  <div>
                    <strong>{text('推进一个研究问题', 'Explore a research question')}</strong>
                    <small>
                      {text('文献、证据与实验思路', 'Papers, evidence and experiments')}
                    </small>
                  </div>
                  <ArrowUpRight size={14} />
                </Link>
              )}
              <Link href={scopedUrl('/projects', workspace)}>
                <FolderOpen size={18} />
                <div>
                  <strong>{text('我的项目', 'My projects')}</strong>
                  <small>
                    {text('独立空间，按需连接记忆', 'Separate spaces, connected memories')}
                  </small>
                </div>
                <ArrowUpRight size={14} />
              </Link>
            </section>
            <div className="study-columns">
              <div className="study-main">
                <section aria-labelledby="study-plan-title" className="study-plan">
                  <div className="study-section-heading">
                    <h2 id="study-plan-title">{text('今天的安排', 'Your plan for today')}</h2>
                    <span>
                      {text(`约 ${plan?.minutes ?? 0} 分钟`, `About ${plan?.minutes ?? 0} min`)}
                    </span>
                  </div>
                  <div className="study-time-bar">
                    <span>
                      <Clock3 size={16} />
                      {text('今天有多少时间？', 'How much time do you have?')}
                    </span>
                    <div role="group" aria-label={text('今日学习时间', 'Study time today')}>
                      {[15, 30, 45, 60, 90].map(minutes => (
                        <button
                          key={minutes}
                          aria-pressed={plan?.budget === minutes}
                          aria-label={text(`${minutes} 分钟`, `${minutes} minutes`)}
                          disabled={saving || !snapshot?.preferences}
                          onClick={() => void save({ minutes })}
                        >
                          {minutes}
                          <small>{text(' 分钟', ' min')}</small>
                        </button>
                      ))}
                    </div>
                  </div>
                  <p className="study-section-help">
                    {text(
                      '先接续待答问题，再做到期复习，最后推进最近的学习。用时是建议，你可以随时调整。',
                      'Unfinished questions first, then due reviews and recent learning. Times are suggestions; adjust your pace anytime.'
                    )}
                  </p>
                  {plan?.selected.length ? (
                    <ol className="study-plan-list">
                      {plan.selected.map((item, index) => (
                        <StudyRow
                          key={item.key}
                          item={item}
                          index={index + 1}
                          zh={zh}
                          disabled={saving || !snapshot?.preferences}
                          defer={() =>
                            void save({ deferral: { key: item.key, action: 'tomorrow' } })
                          }
                        />
                      ))}
                    </ol>
                  ) : (
                    <div className="study-empty">
                      <BookOpen size={23} />
                      <p>
                        {plan?.deferred.length
                          ? text(
                              '已把这些学习安排留到明天，也可以随时放回今天。',
                              'Your learning is set aside for tomorrow. Bring it back whenever you like.'
                            )
                          : text(
                              '还没有需要安排的学习记录。',
                              'There is no learning to schedule yet.'
                            )}
                      </p>
                      <Link
                        href={scopedUrl(
                          readingAllowed ? '/learning/reading' : '/learning',
                          workspace
                        )}
                      >
                        {text('打开我的学习', 'Open my learning')} →
                      </Link>
                    </div>
                  )}
                </section>
                {!!plan?.deferred.length && (
                  <section
                    className="study-deferred"
                    aria-label={text('明天再学', 'Deferred until tomorrow')}
                  >
                    <h2>{text('明天再学', 'For tomorrow')}</h2>
                    <p>
                      {text(
                        '只是暂时移出今日安排，不会修改原有复习排期。',
                        'Hidden from today’s plan. The original review schedule is unchanged.'
                      )}
                    </p>
                    {plan.deferred.map(item => (
                      <div key={item.key}>
                        <span>{item.title}</span>
                        <button
                          disabled={saving}
                          onClick={() =>
                            void save({ deferral: { key: item.key, action: 'restore' } })
                          }
                        >
                          {text('放回今天', 'Bring back')}
                        </button>
                      </div>
                    ))}
                  </section>
                )}
                {!!plan?.later.length && (
                  <details className="study-more">
                    <summary>
                      {text(
                        `还有 ${plan.later.length} 项，可以慢慢来`,
                        `${plan.later.length} more, at your pace`
                      )}
                      <ChevronDown size={15} />
                    </summary>
                    {plan.later.map(item => (
                      <Link key={item.key} href={item.href}>
                        <span>{item.title}</span>
                        <ArrowUpRight size={14} />
                      </Link>
                    ))}
                  </details>
                )}
              </div>
              <aside className="study-side">
                {chatAllowed && (
                  <section className={`study-evidence ${reviewed === 0 ? 'is-empty' : ''}`}>
                    <div className="study-eyebrow">{text('学习足迹', 'YOUR PROGRESS')}</div>
                    <h2>{text('每一次练习，都算数。', 'Every practice leaves a trace.')}</h2>
                    <div className="study-evidence-number">
                      {reviewed === undefined ? '—' : reviewed}
                      <span>{text('道复习题', 'reviewed questions')}</span>
                    </div>
                    <p>
                      {text(
                        '记录独立作答，也记录看过提示后的进步。',
                        'Track independent attempts and progress made with a little help.'
                      )}
                    </p>
                    <Link href={scopedUrl('/learning/practice', workspace)}>
                      {text('查看复习记录', 'Review history')}
                      <ArrowRight size={14} />
                    </Link>
                  </section>
                )}
                {chatAllowed && (
                  <section className="study-paths">
                    <div className="study-section-heading">
                      <h2>{text('正在学习', 'Your learning goals')}</h2>
                      <Route size={17} />
                    </div>
                    {validGoals.length ? (
                      validGoals.slice(0, 3).map(topic => (
                        <Link
                          key={topic.path_id}
                          href={scopedUrl(
                            `/learning/mastery/${encodeURIComponent(topic.path_id)}`,
                            workspace
                          )}
                        >
                          <strong>{topic.name || topic.metadata.goal}</strong>
                          <span>
                            {text(
                              `${topic.map.counts.mastered} / ${topic.map.counts.total} 个目标已达标`,
                              `${topic.map.counts.mastered} / ${topic.map.counts.total} objectives met`
                            )}
                          </span>
                        </Link>
                      ))
                    ) : (
                      <p>
                        {text(
                          '把“想学会什么”写成一个目标，再和导师一起调整路径。',
                          'Turn something you want to learn into a goal, then shape the path with your tutor.'
                        )}
                      </p>
                    )}
                    <Link
                      className="study-text-link"
                      href={scopedUrl('/learning/mastery?create=1', workspace)}
                    >
                      {text('建立学习目标', 'Set a learning goal')}
                      <ArrowUpRight size={14} />
                    </Link>
                  </section>
                )}
              </aside>
            </div>
          </>
        )}
        {!auth.learningPolicy && <Continuations workspace={workspace} />}
      </div>
    </FeaturePage>
  )
}

function StudyRow({
  item,
  index,
  zh,
  disabled,
  defer,
}: {
  item: StudySuggestion
  index: number
  zh: boolean
  disabled: boolean
  defer: () => void
}) {
  const Icon = icons[item.kind]
  return (
    <li className="study-row">
      <span className="study-row-number">{String(index).padStart(2, '0')}</span>
      <div className="study-row-body">
        <div className="study-row-meta">
          <Icon size={13} />
          {labels[item.kind][zh ? 0 : 1]}
          <span>· {zh ? `约 ${item.minutes} 分钟` : `~${item.minutes} min`}</span>
        </div>
        <Link className="study-row-title" href={item.href}>
          {item.title}
          <ArrowUpRight size={15} />
        </Link>
        {item.context && item.context !== item.title && (
          <p className="study-row-context">{item.context}</p>
        )}
        <div className="study-row-actions">
          <details>
            <summary>
              {zh ? '为什么安排这项' : 'Why this suggestion'}
              <ChevronDown size={12} />
            </summary>
            <p>{reasons[item.reason][zh ? 0 : 1]}</p>
            <Link href={item.evidenceHref}>{zh ? '查看原记录' : 'View original record'} →</Link>
          </details>
          <button disabled={disabled} onClick={defer}>
            {zh ? '明天再学' : 'Tomorrow'}
          </button>
        </div>
      </div>
    </li>
  )
}

/** Decorative book illustration; it never stands in for real progress data. */
function StudyDeskArt() {
  return (
    <svg className="study-desk-art" viewBox="0 0 240 180" fill="none" aria-hidden="true">
      <circle cx="133" cy="86" r="75" stroke="currentColor" strokeOpacity=".13" />
      <circle cx="133" cy="86" r="57" stroke="currentColor" strokeOpacity=".08" />
      <path d="M33 117 136 144 210 108" stroke="currentColor" strokeOpacity=".2" />
      <g transform="rotate(-12 117 88)">
        <rect x="64" y="33" width="110" height="127" rx="5" fill="#172d25" />
        <rect x="69" y="28" width="110" height="127" rx="5" fill="#dce3ca" />
        <path d="M82 28v127" stroke="#b7c2a4" />
        <path d="M101 57h52M101 64h34" stroke="#687861" strokeWidth="2" strokeLinecap="round" />
        <path
          d="M101 81h53M101 90h45M101 99h49M101 108h36"
          stroke="#a1ae94"
          strokeWidth="2"
          strokeLinecap="round"
        />
        <path d="M149 28v32l8-6 8 6V28" fill="#c88765" />
        <circle cx="145" cy="130" r="9" stroke="#687861" />
        <path d="m141 130 3 3 5-6" stroke="#687861" strokeWidth="1.5" />
      </g>
      <path d="m44 52 3-9 3 9 9 3-9 3-3 9-3-9-9-3 9-3Z" fill="#c88765" />
      <circle cx="203" cy="137" r="3" fill="#dce3ca" />
      <path d="m197 38 6 6m0-6-6 6" stroke="#dce3ca" strokeOpacity=".6" />
    </svg>
  )
}
