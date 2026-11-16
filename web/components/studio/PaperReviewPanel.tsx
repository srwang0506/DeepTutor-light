'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { useTranslation } from 'react-i18next'
import { Plus, RefreshCw } from 'lucide-react'
import { Dialog } from '@/shared/ui/Dialog'
import { Button } from '@/shared/ui/Button'
import { useRecoveryPrincipal } from '@/hooks/useRecoveryPrincipal'
import { recoveryPrincipal } from '@/lib/recovery-principal'
import { activeWorkspaceId, scopedUrl } from '@/lib/workspace-scope'
import { randomUuid } from '@/lib/random-uuid'
import { StudioRequestError } from '@/lib/studio-api'
import { studioReferenceHref, type StudioSourceReference } from '@/lib/studio-references'
import {
  createPaperReview, ensurePaperReviewSession, exportPaperReview, getPaperReview,
  listPaperReviews, paperReviewOptions, reviewMatchesEdition, updatePaperReviewIssue,
  type CreatePaperReview, type PaperReview, type PaperReviewIssue, type PaperReviewMode, type PaperReviewTarget,
} from '@/lib/paper-review-api'
import { ErrorNotice, Field } from './StudioShell'
import StudioTutorCompanion from './StudioTutorCompanion'
import './paper-review.css'

const MODES: [PaperReviewMode, string][] = [
  ['self', '投稿前自查'], ['full', '完整模拟审稿'], ['quick', '快速审稿'], ['rebuttal', '回复评估'], ['revise', '修改复核'],
]
const PENDING_LABELS: Record<string, string> = {
  'Review has not been run.': '审稿尚未运行。',
  '审稿尚未运行。': '审稿尚未运行。',
  '科学判断仍需作者复核。': '科学判断仍需作者复核。',
  'Scientific judgments require author review.': '科学判断仍需作者复核。',
  '外部来源、创新性检索范围与独立复核仍需检查。': '外部来源、创新性检索范围与独立复核仍需检查。',
  'External-source provenance, novelty coverage and independent critique require review.': '外部来源、创新性检索范围与独立复核仍需检查。',
  '技能校验尚未全部通过；详细诊断保存在导出记录中。': '技能校验尚未全部通过；详细诊断保存在导出记录中。',
  'Skill validation is incomplete; the export contains the original diagnostics.': '技能校验尚未全部通过；详细诊断保存在导出记录中。',
  '技能校验未能完成。': '技能校验未能完成。',
  'Upstream validation did not complete.': '技能校验未能完成。',
}
const CHECK_LABELS: Record<string, string> = { upstream: '结构检查', quotes: '原文定位', scientific_claims: '科学判断' }
const CHECK_VALUES: Record<string, string> = {
  not_run: '尚未检查', matched: '已核对引用位置', needs_review: '需要核对',
  passed: '结构检查通过', incomplete: '检查未完成', not_automatically_verified: '仍需作者复核',
}

interface Props {
  workspace: string
  target: PaperReviewTarget
  active?: boolean
  initialReviewId?: string
  onJump?: (reference: StudioSourceReference, page: number, quote: string) => void
}

export default function PaperReviewPanel(props: Props) {
  const principal = useRecoveryPrincipal()
  const { t } = useTranslation()
  const target = props.target
  const key = JSON.stringify([principal, props.workspace, target.kind, target.kind === 'edition'
    ? [target.reference.scope, target.reference.id, target.reference.revision, target.reference.locator?.blob_hash]
    : [target.manuscriptId, target.buildId]])
  return principal ? <ReviewPanel key={key} {...props} principal={principal} />
    : <p role="status" className="paper-review-note">{t('正在加载审稿记录…')}</p>
}

function ReviewPanel({ workspace, target, active = true, initialReviewId = '', onJump, principal }: Props & { principal: string }) {
  const { t, i18n } = useTranslation()
  const mounted = useRef(false)
  const [reviews, setReviews] = useState<PaperReview[]>([])
  const [selected, setSelected] = useState('')
  const [view, setView] = useState<'issues' | 'conversation'>('issues')
  const [loading, setLoading] = useState(true)
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [error, setError] = useState('')
  const [loadError, setLoadError] = useState('')
  const [refreshError, setRefreshError] = useState<{ id: string; message: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [dialog, setDialog] = useState(false)
  const [mode, setMode] = useState<PaperReviewMode>('self')
  const [venue, setVenue] = useState('')
  const [venues, setVenues] = useState<{ id: string; label: string }[]>([])
  const [venueError, setVenueError] = useState(false)
  const [instructions, setInstructions] = useState('')
  const [requestPending, setRequestPending] = useState(false)
  const request = useRef<CreatePaperReview | null>(null)
  const lock = useRef(false)
  const [start, setStart] = useState<{ reviewId: string; nonce: string; text: string } | null>(null)
  const current = reviews.find(row => row.id === selected) ?? null
  const sourceId = target.kind === 'edition' ? target.reference.id : target.manuscriptId
  const sourceRevision = target.kind === 'edition' ? target.reference.revision : target.buildId
  const sourceHash = target.kind === 'edition' ? target.reference.locator?.blob_hash : ''
  const sourceScope = target.kind === 'edition' ? target.reference.scope : ''
  const targetKind = target.kind
  const isCurrent = useCallback(() => mounted.current && recoveryPrincipal() === principal && activeWorkspaceId() === workspace, [principal, workspace])
  const put = useCallback((row: PaperReview) => {
    setReviews(previous => {
      const existing = previous.find(item => item.id === row.id)
      return existing && existing.revision > row.revision ? previous : [row, ...previous.filter(item => item.id !== row.id)]
    })
  }, [])

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])
  useEffect(() => {
    if (!dialog) return
    const controller = new AbortController()
    void paperReviewOptions(workspace, controller.signal).then(result => {
      if (!controller.signal.aborted && isCurrent()) { setVenues(result.venues); setVenueError(false) }
    }).catch(() => { if (!controller.signal.aborted && isCurrent()) setVenueError(true) })
    return () => controller.abort()
  }, [dialog, workspace, isCurrent])
  useEffect(() => {
    const controller = new AbortController()
    const selectedTarget: PaperReviewTarget = targetKind === 'edition'
      ? { kind: 'edition', reference: { kind: 'edition', id: sourceId, scope: sourceScope, revision: Number(sourceRevision), locator: { blob_hash: sourceHash } } }
      : { kind: 'manuscript', manuscriptId: sourceId, buildId: sourceRevision as string | null }
    void listPaperReviews(workspace, selectedTarget, controller.signal).then(result => {
      if (controller.signal.aborted || !isCurrent()) return
      const rows = selectedTarget.kind === 'edition'
        ? result.reviews.filter(row => reviewMatchesEdition(row, selectedTarget.reference))
        : result.reviews
      setReviews(previous => {
        const merged = new Map(rows.map(row => [row.id, row]))
        for (const row of previous) if (!merged.has(row.id) || merged.get(row.id)!.revision < row.revision) merged.set(row.id, row)
        return [...merged.values()].sort((a, b) => b.created - a.created)
      })
      setSelected(previous => previous || (initialReviewId ? rows.find(row => row.id === initialReviewId)?.id || '' : rows[0]?.id || ''))
      setLoading(false)
      setLoadError('')
    }).catch(caught => {
      if (!controller.signal.aborted && isCurrent()) {
        setLoading(false)
        setLoadError(caught instanceof Error ? caught.message : t('暂时无法加载审稿记录。'))
      }
    })
    return () => controller.abort()
  }, [workspace, targetKind, sourceId, sourceRevision, sourceHash, sourceScope, loadAttempt, initialReviewId, isCurrent, t])

  const refresh = useCallback(async () => {
    if (!selected || !isCurrent()) return
    try {
      const row = await getPaperReview(workspace, selected)
      if (isCurrent()) {
        put(row)
        setRefreshError(previous => previous?.id === selected ? null : previous)
      }
    } catch (caught) {
      if (isCurrent()) setRefreshError({ id: selected, message: caught instanceof Error ? caught.message : t('暂时无法加载审稿记录。') })
    }
  }, [selected, workspace, isCurrent, put, t])

  // Checkpoints are durable even while the user is reading an issue instead
  // of watching the native chat. Never poll another account or hidden page.
  useEffect(() => {
    if (!active || !selected) return
    const timer = window.setInterval(() => void refresh(), 5000)
    return () => window.clearInterval(timer)
  }, [active, selected, refresh])

  async function run(action: () => Promise<void>) {
    if (lock.current || !isCurrent()) return false
    lock.current = true
    setBusy(true)
    setError('')
    try { await action(); return true }
    catch (caught) { if (isCurrent()) setError(caught instanceof Error ? caught.message : t('操作未完成，请重试。')); return false }
    finally { lock.current = false; if (isCurrent()) setBusy(false) }
  }

  async function create() {
    if (!request.current) {
      request.current = {
        request_id: randomUuid(), mode, venue: venue.trim() || 'general', instructions: instructions.trim(),
        language: (i18n.resolvedLanguage || i18n.language).startsWith('zh') ? 'zh' : 'en',
        ...(target.kind === 'edition'
          ? { edition_id: target.reference.id, revision: target.reference.revision, blob_hash: target.reference.locator?.blob_hash }
          : { manuscript_id: target.manuscriptId, build_id: target.buildId || undefined }),
      }
      setRequestPending(true)
    }
    const nonce = request.current.request_id
    let created: PaperReview
    try { created = await createPaperReview(workspace, request.current) }
    catch (caught) {
      if (caught instanceof StudioRequestError && [409, 422].includes(caught.status) && isCurrent()) {
        request.current = null
        setRequestPending(false)
      }
      throw caught
    }
    if (!isCurrent()) return
    put(created)
    const bound = await ensurePaperReviewSession(workspace, created.id)
    if (!isCurrent()) return
    if (!bound.data.session_id) throw new Error(t('审稿对话尚未就绪，请重试。'))
    put(bound)
    setSelected(bound.id)
    setStart({ reviewId: bound.id, nonce, text: t('请按本次审稿任务的模式和目标会议审阅所附稿件，逐条给出有原文依据的意见和修改建议，并保存审稿进度。') })
    setView('conversation')
    setDialog(false)
    request.current = null
    setRequestPending(false)
  }

  async function openConversation() {
    if (!current) return
    const bound = await ensurePaperReviewSession(workspace, current.id)
    if (!isCurrent()) return
    if (!bound.data.session_id) throw new Error(t('审稿对话尚未就绪，请重试。'))
    put(bound)
    setView('conversation')
  }

  const canStart = target.kind === 'edition' ? !!target.reference.locator?.blob_hash : !!target.buildId
  const currentBuild = target.kind === 'manuscript' && current?.data.build_id === target.buildId
  const canJumpInline = !!current && !!onJump && (target.kind === 'edition'
    ? reviewMatchesEdition(current, target.reference) : currentBuild)
  const modeLabel = (value: string) => t(MODES.find(([id]) => id === value)?.[1] || value)
  const reviewContext = current?.data.session_id ? {
    reference: current.review_ref,
    sessionId: current.data.session_id,
    mode: current.data.mode,
    allowInlineJump: canJumpInline,
    onCheckpoint: refresh,
    start: start?.reviewId === current.id ? start : null,
    onStartAttempted: () => setStart(null),
  } : undefined

  return (
    <section className="paper-review-panel" aria-label={t('模拟审稿')}>
      <header className="paper-review-toolbar">
        <strong>{t('模拟审稿')}</strong>
        <Button variant="ghost" size="sm" disabled={busy || !canStart} onClick={() => {
          setError(''); setDialog(true)
        }}><Plus size={14} aria-hidden />{t('新审稿')}</Button>
      </header>
      <ErrorNotice message={dialog ? '' : error || loadError || (refreshError?.id === selected ? refreshError.message : '')} />
      {!loading && !selected && initialReviewId && !loadError && <ErrorNotice message={t('审稿对话无法打开，原稿版本或会话归属需要重新核对。')} />}
      {!canStart && <p className="paper-review-note">{t('先编译出一份 PDF，再对该版本进行审稿。')}</p>}
      {loading ? <p role="status" className="paper-review-note">{t('正在加载审稿记录…')}</p> : loadError ? (
        <Button variant="secondary" size="sm" onClick={() => { setLoading(true); setLoadAttempt(value => value + 1) }}>{t('Retry')}</Button>
      ) : !reviews.length ? (
        <p className="paper-review-note">{t('还没有审稿记录。审稿会绑定当前原稿版本。')}</p>
      ) : null}
      {!!reviews.length && (
        <div className="paper-review-controls">
          <label className="paper-review-history">
            <span className="sr-only">{t('审稿记录')}</span>
            <select value={selected} disabled={busy} onChange={event => { setSelected(event.target.value); setView('issues'); setError('') }}>
              {!selected && <option value="" disabled>{t('审稿记录')}</option>}
              {reviews.map(row => <option key={row.id} value={row.id}>
                {modeLabel(row.data.mode)} · {row.data.venue === 'general' ? t('通用') : row.data.venue} · {new Date(row.created * 1000).toLocaleDateString(i18n.resolvedLanguage || i18n.language)}
              </option>)}
            </select>
          </label>
          <div className="paper-review-tabs" role="group" aria-label={t('审稿视图')}>
            <button type="button" aria-pressed={view === 'issues'} onClick={() => setView('issues')}>{t('意见')} {current?.data.issues.length || ''}</button>
            <button type="button" aria-pressed={view === 'conversation'} disabled={busy} onClick={() => void run(openConversation)}>{t('对话')}</button>
            <button type="button" aria-label={t('刷新审稿记录')} disabled={busy} onClick={() => void refresh()}><RefreshCw size={13} aria-hidden /></button>
          </div>
        </div>
      )}
      {current && (
        <>
          <div className="paper-review-issues" hidden={view !== 'issues'}>
            <div className="paper-review-summary">
              <span>{t('审稿草稿')}</span>
              <details className="paper-review-export"><summary>{t('导出')}</summary>
                <button type="button" disabled={busy} onClick={() => void run(() => exportPaperReview(workspace, current.id, 'markdown'))}>{t('Markdown 文件')}</button>
                <button type="button" disabled={busy} onClick={() => void run(() => exportPaperReview(workspace, current.id, 'json'))}>{t('JSON 文件')}</button>
              </details>
            </div>
            {!current.data.checkpoint_hash && <p className="paper-review-note">{t('尚未保存审稿结果。请在对话中开始或继续审稿。')}</p>}
            {!!current.data.pending.length && <details className="paper-review-checks">
              <summary>{t('待完成检查（{{count}}）', { count: current.data.pending.length })}</summary>
              <ul>{current.data.pending.filter(item => item in PENDING_LABELS).map((item, index) => <li key={index}>{t(PENDING_LABELS[item])}</li>)}</ul>
              {current.data.pending.some(item => !(item in PENDING_LABELS)) && <details>
                <summary>{t('原始检查报告')}</summary>
                <ul>{current.data.pending.filter(item => !(item in PENDING_LABELS)).map((item, index) => <li key={index}>{item}</li>)}</ul>
              </details>}
            </details>}
            {!!Object.keys(current.data.checks).length && <details className="paper-review-checks">
              <summary>{t('查看检查记录')}</summary>
              <dl>{Object.entries(current.data.checks).filter(([name, value]) => name in CHECK_LABELS && value in CHECK_VALUES)
                .map(([name, value]) => <div key={name}><dt>{t(CHECK_LABELS[name])}</dt><dd>{t(CHECK_VALUES[value])}</dd></div>)}</dl>
              {Object.entries(current.data.checks).some(([name, value]) => !(name in CHECK_LABELS) || !(value in CHECK_VALUES)) && <details>
                <summary>{t('原始检查报告')}</summary>
                <dl>{Object.entries(current.data.checks).filter(([name, value]) => !(name in CHECK_LABELS) || !(value in CHECK_VALUES))
                  .map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl>
              </details>}
            </details>}
            {target.kind === 'manuscript' && !currentBuild && <p className="paper-review-note">{t('这次审稿来自先前构建，原文链接会打开当时的 PDF。')}</p>}
            {current.data.issues.map(issue => <ReviewIssueRow key={`${current.id}:${issue.id}`} issue={issue} review={current} workspace={workspace}
              busy={busy} onJump={canJumpInline ? onJump : undefined}
              onSave={(revision, status, response) => run(async () => {
                const row = await updatePaperReviewIssue(workspace, { ...current, revision }, issue, status, response)
                if (isCurrent()) put(row)
              })} />)}
            {!!current.data.issues.length && target.kind === 'manuscript' && <p className="paper-review-note">{t('在编辑器选中要修改的段落，再使用“AI 修改选区”审阅修改。意见不会自动写入稿件。')}</p>}
            {!current.data.issues.length && !!current.data.checkpoint_hash && <p className="paper-review-note">{t('这个检查点尚无逐条意见；这不表示稿件已通过全部检查。')}</p>}
          </div>
          {current.data.session_id && <div className="paper-review-conversation" hidden={view !== 'conversation'}>
            <StudioTutorCompanion key={`${current.id}:${current.data.session_id}`} workspace={workspace}
              reference={current.data.target_ref} paperTitle={current.data.title} initialSessionId={current.data.session_id || ''}
              prompt={null} onSessionBound={() => {}} onJump={(page, hash) => {
                if (canJumpInline && (!hash || hash === current.data.target_ref.locator?.blob_hash)) onJump?.(current.data.target_ref, page, '')
              }} onClearSelection={() => {}} practiceBusy={false} active={active && view === 'conversation'} review={reviewContext} />
          </div>}
        </>
      )}
      <Dialog open={dialog} onClose={() => { if (!busy) setDialog(false) }} busy={busy} title={t('模拟审稿')} size="md" className="studio-dialog"
        footer={<Button disabled={busy || !canStart} loading={busy} onClick={() => void run(create)}>{requestPending ? t('重试开始审稿') : t('开始审稿')}</Button>}>
        <ErrorNotice message={error} />
        <fieldset disabled={busy || requestPending} className="studio-form">
          <Field label={t('审稿模式')}><select value={mode} onChange={event => setMode(event.target.value as PaperReviewMode)}>
            {MODES.map(([id, label]) => <option key={id} value={id}>{t(label)}</option>)}
          </select></Field>
          <Field label={t('目标会议或期刊')}><select value={venue || 'general'} onChange={event => setVenue(event.target.value)}>
            <option value="general">{t('通用')}</option>
            {venues.filter(item => item.id !== 'general').map(item => <option key={item.id} value={item.id} title={item.label}>{item.id}</option>)}
          </select></Field>
          {venueError && <p className="studio-note">{t('会议列表暂不可用，可先使用通用标准。')}</p>}
          <Field label={t('补充要求')}><textarea rows={3} value={instructions} onChange={event => setInstructions(event.target.value)} maxLength={8000} placeholder={t('例如：重点检查贡献表述、实验对照和结论范围。')} /></Field>
        </fieldset>
      </Dialog>
    </section>
  )
}

function ReviewIssueRow({ issue, review, workspace, busy, onJump, onSave }: {
  issue: PaperReviewIssue
  review: PaperReview
  workspace: string
  busy: boolean
  onJump?: Props['onJump']
  onSave: (revision: number, status: PaperReviewIssue['tracking_status'], response: string) => Promise<boolean>
}) {
  const { t } = useTranslation()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState({ revision: review.revision, status: issue.tracking_status, response: issue.response })
  const located = issue.location_status === 'matched' && Number.isSafeInteger(issue.page) && Number(issue.page) > 0 && !!issue.quote.trim()
  const reference = { ...review.data.target_ref, locator: { ...review.data.target_ref.locator, page: issue.page || undefined } }
  const tracking: Record<string, string> = { open: '待处理', working: '修订中', addressed: '已回应' }
  const severity: Record<string, string> = { critical: '关键问题', major: '主要问题', minor: '次要问题', suggestion: '建议' }
  return <details className="paper-review-issue">
    <summary><strong>{issue.title}</strong><span>{t(severity[issue.severity] || issue.severity)}</span><span>{t(tracking[issue.tracking_status] || issue.tracking_status)}</span></summary>
    {issue.body && <p>{issue.body}</p>}
    {located ? <Link className="paper-review-source" href={scopedUrl(studioReferenceHref(reference), workspace)} onClick={event => {
      if (!onJump || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
      event.preventDefault(); onJump(review.data.target_ref, issue.page!, issue.quote)
    }}>{t('第 {{page}} 页', { page: issue.page })}</Link>
      : <span className="paper-review-note">{t('原文位置待核对')}</span>}
    {issue.quote && <blockquote>{issue.quote}</blockquote>}
    {issue.request && <div className="paper-review-request"><strong>{t('修改建议')}</strong><p>{issue.request}</p></div>}
    {!editing ? <>
      {issue.response && <p>{issue.response}</p>}
      <Button variant="ghost" size="sm" disabled={busy} onClick={() => { setDraft({ revision: review.revision, status: issue.tracking_status, response: issue.response }); setEditing(true) }}>{t('记录处理进度')}</Button>
    </> : <div className="paper-review-response">
      <Field label={t('处理状态')}><select value={draft.status} disabled={busy} onChange={event => setDraft(value => ({ ...value, status: event.target.value as PaperReviewIssue['tracking_status'] }))}>
        {Object.entries(tracking).map(([id, label]) => <option key={id} value={id}>{t(label)}</option>)}
      </select></Field>
      <Field label={t('回应或修改说明')}><textarea rows={3} disabled={busy} value={draft.response} onChange={event => setDraft(value => ({ ...value, response: event.target.value }))} /></Field>
      <div className="studio-actions"><Button size="sm" disabled={busy || (draft.status === 'addressed' && !draft.response.trim())} onClick={async () => { if (await onSave(draft.revision, draft.status, draft.response)) setEditing(false) }}>{t('保存')}</Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => setEditing(false)}>{t('取消')}</Button></div>
    </div>}
  </details>
}
