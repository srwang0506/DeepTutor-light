'use client'
import { savePdfOffline } from '@/lib/offline-study'
import { useTranslation } from 'react-i18next'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import { useParams, useSearchParams } from 'next/navigation'
import dynamic from 'next/dynamic'
import { ArrowLeft, BookmarkPlus, Columns2, MessageCircle } from 'lucide-react'
import { PdfDocumentView, type SelectionPayload } from '@/components/reading/PdfDocumentView'
import type { AnnotationItem } from '@/lib/reading-api'
import { useActiveWorkspaceId } from '@/hooks/useActiveWorkspaceId'
import { useRecoveryPrincipal } from '@/hooks/useRecoveryPrincipal'
import StudioLoading from './StudioLoading'
import { scopedUrl } from '@/lib/workspace-scope'
import { randomUuid } from '@/lib/random-uuid'
import { useAuthStatus } from '@/hooks/useAuthStatus'
import { useCapabilityAccess } from '@/components/access/CapabilityAccessContext'
import type { QuizQuestion } from '@/lib/quiz-types'
import type { StudioSourceReference } from '@/lib/studio-references'
import {
  studio,
  studioUrl,
  post,
  records,
  type Edition,
  type EvidenceData,
  type StudioRecord,
  type Work,
} from '@/lib/studio-api'
import { Action, ErrorNotice, Field, useOperation } from './StudioShell'

const StudioTutorCompanion = dynamic(() => import('./StudioTutorCompanion'), { ssr: false })
const PaperReviewPanel = dynamic(() => import('./PaperReviewPanel'), { ssr: false })
const StudyQuizViewer = dynamic(() => import('@/components/quiz/StudyQuizViewer'), { ssr: false })
type InlinePractice = { session_id: string; turn_id: string; questions: QuizQuestion[] }

export default function EditionReader() {
  const principal = useRecoveryPrincipal()
  const workspace = useActiveWorkspaceId()
  const params = useParams<{ edition: string }>()
  const search = useSearchParams()
  const value = Number(search.get('revision'))
  const revision = Number.isSafeInteger(value) && value > 0 ? value : null
  if (!principal) return <StudioLoading label="正在打开原文…" />
  return (
    <Reader
      key={JSON.stringify([principal, workspace, params.edition, revision, search.get('review') || ''])}
      workspace={workspace}
      identifier={params.edition}
      revision={revision}
    />
  )
}
function Reader({ workspace, identifier, revision }: { workspace: string; identifier: string; revision: number | null }) {
  const { t } = useTranslation()
  const search = useSearchParams()
  const op = useOperation()
  const auth = useAuthStatus()
  const access = useCapabilityAccess()
  const canTutor = !auth.loading && (!auth.learningPolicy || auth.learningPolicy.allowedSurfaces.includes('chat')) && access.has('llm')
  const [initialSession] = useState(search.get('session') || '')
  const [initialReview] = useState(search.get('review') || '')
  const [panel, setPanel] = useState<'evidence' | 'tutor' | 'practice' | 'review'>(initialReview ? 'review' : initialSession ? 'tutor' : 'evidence')
  const [openedTutor, setOpenedTutor] = useState(Boolean(initialSession))
  const [prompt, setPrompt] = useState<{ text: string; nonce: number } | null>(null)
  const [quiz, setQuiz] = useState<{ record: string; data: InlinePractice } | null>(null)
  const asideRef = useRef<HTMLElement>(null)
  const openPanel = useCallback((next: 'evidence' | 'tutor' | 'practice' | 'review') => {
    setPanel(next)
    if (next === 'tutor') setOpenedTutor(true)
    if (window.matchMedia('(max-width: 760px)').matches) {
      requestAnimationFrame(() => asideRef.current?.scrollIntoView({ block: 'nearest' }))
    }
  }, [])
  const bindSession = useCallback((id: string) => {
    const url = new URL(window.location.href)
    if (url.pathname !== `/library/${encodeURIComponent(identifier)}`) return
    const changed = id ? url.searchParams.get('session') !== id : url.searchParams.has('session')
    if (!changed) return
    if (id) url.searchParams.set('session', id)
    else url.searchParams.delete('session')
    window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`)
  }, [identifier])
  const [edition, setEdition] = useState<Edition | null>(null),
    [title, setTitle] = useState(t('打开原文…')),
    [evidence, setEvidence] = useState<StudioRecord<EvidenceData>[]>([]),
    [related, setRelated] = useState<Edition[]>([])
  const [hash, setHash] = useState(search.get('file') || ''),
    [page, setPage] = useState(() => {
      const requested = Number(search.get('page'))
      return Number.isSafeInteger(requested) && requested > 0 ? requested : 1
    }),
    [selection, setSelection] = useState<SelectionPayload | null>(null)
  const [label, setLabel] = useState(''),
    [note, setNote] = useState(''),
    [kind, setKind] = useState('quote'),
    [confirmed, setConfirmed] = useState(false),
    [saved, setSaved] = useState(''),
    [compare, setCompare] = useState('')
  const [jump, setJump] = useState<{ locator: number; quote?: string; nonce: number } | null>({
    locator: page,
    nonce: 0,
  })
  const [loadAttempt, setLoadAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    void studio<Edition>(workspace, `/record/${identifier}${revision ? `?revision=${revision}` : ''}`, {}, controller.signal)
      .then(async (e) => {
        const [work, ev] = await Promise.all([
          studio<Work>(workspace, `/works/${e.data.work_id}`, {}, controller.signal),
          records<EvidenceData>(
            workspace,
            `evidence?q=${encodeURIComponent(identifier)}`,
            controller.signal,
          ),
        ])
        if (controller.signal.aborted) return
        setEdition(e)
        setTitle(work.data.title || e.data.label)
        setRelated(
          work.editions.filter((other) => other.id !== identifier && other.data.files.length),
        )
        setEvidence(ev)
        if (!hash) setHash(e.data.files[0]?.hash || '')
        if (hash && !e.data.files.some(file => file.hash === hash)) {
          op.setError(t('链接中的原件不属于此版本。请选择此版本的原件后继续。'))
        } else op.setError('')
      })
      .catch((e) => {
        if (!controller.signal.aborted) op.setError(e.message)
      })
    return () => controller.abort()
  }, [identifier, workspace, revision, loadAttempt]) // eslint-disable-line react-hooks/exhaustive-deps
  const practiceKey = useRef('')
  useEffect(() => {
    practiceKey.current = randomUuid()
  }, [selection])
  useEffect(() => {
    if (!edition || !hash || !page || !edition.data.files.some(file => file.hash === hash && page <= file.pages)) return
    const timer = setTimeout(() => {
      void post(workspace, '/resume', {
        scope: edition.scope,
        kind: 'edition',
        id: edition.id,
        revision: edition.revision,
        locator: { page, blob_hash: hash },
      }).catch(() => {})
    }, 1200)
    return () => clearTimeout(timer)
  }, [edition, hash, page, workspace])
  async function practice() {
    if (!edition || !selection) return
    const result = await post<{
      operation_id: string
      committed: boolean
      failure_reason?: string
    }>(workspace, '/actions', {
      action: 'practice_from_selection',
      idempotency_key: practiceKey.current,
      target: {
        scope: edition.scope,
        kind: 'edition',
        id: edition.id,
        revision: edition.revision,
        locator: { blob_hash: hash, page: selection.locator, quote: selection.quote },
      },
    })
    if (!result.committed)
      throw new Error(result.failure_reason || t('练习正在生成，稍后可使用同一选区重试。'))
    const activity = await studio<StudioRecord<InlinePractice>>(workspace, `/record/${result.operation_id}`)
    setQuiz({ record: result.operation_id, data: activity.data })
    openPanel('practice')
  }
  const originalPath = `/editions/${identifier}/files/${hash}${edition ? `?revision=${edition.revision}` : ''}`
  const file = edition?.data.files.find((f) => f.hash === hash),
    other = related.find((e) => e.id === compare)
  const annotations = useMemo<AnnotationItem[]>(
    () =>
      evidence
        .filter((e) => e.data.blob_hash === hash)
        .map((e) => ({
          annotation_id: e.id,
          locator: e.data.page,
          kind: 'highlight',
          color: e.data.verification === 'confirmed' ? 'green' : 'yellow',
          quote: e.data.quote,
          note: e.data.label,
          rects: e.data.region ? [e.data.region] : [],
          source_anchor: e.data.blob_hash,
          author: 'user',
          created_at: e.created,
          updated_at: e.updated,
        })),
    [evidence, hash],
  )
  async function save() {
    if (!selection || !file) return
    const rects = selection.rects
    const region = rects.length
      ? [
          Math.min(...rects.map((r) => r[0])),
          Math.min(...rects.map((r) => r[1])),
          Math.max(...rects.map((r) => r[2])),
          Math.max(...rects.map((r) => r[3])),
        ]
      : null
    const row = await post<StudioRecord<EvidenceData>>(workspace, '/evidence', {
      edition_id: identifier,
      blob_hash: hash,
      page: selection.locator,
      kind,
      quote: selection.quote,
      region,
      label: label || t("{{value1}} · 第 {{value2}} 页", { value1: title, value2: selection.locator }),
      interpretation: note,
      verification: confirmed ? 'confirmed' : 'unreviewed',
    })
    setEvidence((v) => [row, ...v])
    setSaved(t('摘录已保存，可在证据矩阵中引用。'))
    setSelection(null)
    setNote('')
    setLabel('')
    setConfirmed(false)
  }
  function ask(action: string) {
    if (!selection || !canTutor) return
    setPrompt({ text: action, nonce: Date.now() })
    openPanel('tutor')
  }
  function jumpToSource(page: number, original?: string, quote?: string) {
    const target = edition?.data.files.find(file => file.hash === (original || hash))
    if (!target || !Number.isSafeInteger(page) || page < 1 || page > target.pages) return
    if (target.hash !== hash) {
      setHash(target.hash)
      setSelection(null)
      op.setError('')
      // An explicit file change repairs the shareable link as well as the
      // current view. Ordinary scrolling still only updates reading progress.
      const url = new URL(window.location.href)
      if (url.pathname === `/library/${encodeURIComponent(identifier)}`) {
        url.searchParams.set('file', target.hash)
        url.searchParams.set('page', String(page))
        window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`)
      }
    }
    setPage(page)
    setJump({ locator: page, ...(quote ? { quote } : {}), nonce: Date.now() })
  }
  const reference: StudioSourceReference | null = edition && file ? {
    scope: edition.scope,
    id: edition.id,
    kind: 'edition',
    revision: edition.revision,
    locator: {
      blob_hash: hash,
      page: Math.min(file.pages, selection?.locator || page),
      ...(selection?.quote ? { quote: selection.quote.slice(0, 16000) } : {}),
    },
  } : null
  const outline = file?.outline ?? []
  return (
    <section className="studio-reader">
      <header className="studio-reader-bar">
        <div className="studio-actions">
          <Link aria-label={t('回到资料库')} href={scopedUrl('/library', workspace)}>
            <ArrowLeft size={17} />
          </Link>
          <div>
            <h1>{title}</h1>
            <span className="studio-reader-page">
              {edition?.data.label} · {t('原件保留 · 第 {{page}} / {{total}} 页', { page, total: file?.pages || '—' })}</span>
          </div>
        </div>
        <div className="studio-actions">
          {file && (
            <label className="studio-reader-page-control">
              <span>{t('跳到物理页')}</span>
              <input type="number" min={1} max={file.pages} step={1} value={page}
                className="studio-control"
                onChange={event => jumpToSource(event.target.valueAsNumber)} />
            </label>
          )}
          {edition && edition.data.files.length > 1 && (
            <select aria-label={t('原件与补充材料')} className="studio-control studio-reader-file-control" value={hash}
              onChange={event => jumpToSource(1, event.target.value)}>
              {edition.data.files.map(file => <option key={file.hash} value={file.hash}>{file.name}</option>)}
            </select>
          )}
          {related.length > 0 && (
            <label className="studio-actions" style={{ fontSize: 11 }}>
              <Columns2 size={15} />
              <select
                aria-label={t('对照论文版本')}
                value={compare}
                onChange={(e) => setCompare(e.target.value)}
              >
                <option value="">{t('对照版本')}</option>
                {related.map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.data.label}
                  </option>
                ))}
              </select>
            </label>
          )}
          <Link className="studio-outlink" href={scopedUrl('/studio/evidence', workspace)}>
            {t('查看证据矩阵')}
          </Link>
        </div>
        <button
          type="button"
          className="px-3 py-2 text-sm"
          disabled={!file || op.busy}
          onClick={() =>
            void op.run(async () => {
              const href = await savePdfOffline(
                workspace,
                title,
                studioUrl(originalPath, workspace),
              )
              window.open(href, '_blank', 'noopener')
            })
          }
        >
          {t('保存供离线使用')}
        </button>
      </header>
      <ErrorNotice message={op.error} />
      {op.error && !edition && <button type="button" className="studio-button secondary" onClick={() => setLoadAttempt(value => value + 1)}>{t('Retry')}</button>}
      {edition && hash && !file && edition.data.files[0] && (
        <button type="button" className="studio-button secondary" onClick={() => jumpToSource(1, edition.data.files[0].hash)}>{t('打开此版本的原件')}</button>
      )}
      <div className="studio-reader-body" data-has-outline={outline.length > 0 || undefined}>
        {!!outline.length && (
          <nav className="studio-reader-outline" aria-label={t('论文目录')}>
            <div className="studio-eyebrow" style={{ padding: 8 }}>
              {t('CONTENTS · 目录')}
            </div>
            {outline.map((entry, i) => (
              <button key={i} style={{ paddingLeft: 8 + Math.min(entry.level - 1, 3) * 10 }}
                onClick={() => jumpToSource(entry.page)}>
                {entry.title}
              </button>
            ))}
          </nav>
        )}
        <main
          className="studio-reader-pdf"
          style={other ? { display: 'grid', gridTemplateColumns: '1fr 1fr' } : undefined}
        >
          {file && (
            <PdfDocumentView
              materialId={`${identifier}:${hash}`}
              sourceUrl={studioUrl(originalPath, workspace)}
              unitCount={file.pages}
              annotations={annotations}
              highlightedAnnotationId={search.get('evidence')}
              jump={jump}
              onSelection={(value) => {
                if (value) {
                  setSelection(value)
                  setSaved('')
                }
              }}
              onVisibleLocatorChange={setPage}
              onAnnotationClick={(annotation) => {
                const e = evidence.find((r) => r.id === annotation.annotation_id)
                if (e) {
                  setSaved(e.data.label)
                  setNote(e.data.interpretation)
                }
              }}
            />
          )}
          {other && (
            <PdfDocumentView
              materialId={`${other.id}:${other.data.files[0].hash}`}
              sourceUrl={studioUrl(
                `/editions/${other.id}/files/${other.data.files[0].hash}`,
                workspace,
              )}
              unitCount={other.data.files[0].pages}
              annotations={[]}
              jump={null}
              onSelection={() => {}}
            />
          )}
        </main>
        <aside ref={asideRef} className="studio-reader-aside studio-reader-sidebar">
          <div role="group" aria-label={t('Paper tools')} className="studio-reader-sidebar-tabs studio-pills">
            <button type="button" aria-pressed={panel === 'evidence'} onClick={() => openPanel('evidence')}>{t('原文证据')}</button>
            {canTutor && reference && <button type="button" aria-pressed={panel === 'tutor'} onClick={() => openPanel('tutor')}>{t('Paper tutor')}</button>}
            {canTutor && reference && <button type="button" aria-pressed={panel === 'review'} onClick={() => openPanel('review')}>{t('审稿')}</button>}
            {quiz && <button type="button" aria-pressed={panel === 'practice'} onClick={() => openPanel('practice')}>{t('Practice')}</button>}
          </div>
          {openedTutor && canTutor && reference && (
            <div className="studio-reader-tutor-slot" hidden={panel !== 'tutor'}>
              <StudioTutorCompanion workspace={workspace} reference={reference} paperTitle={title} initialSessionId={initialSession}
                prompt={prompt} onSessionBound={bindSession} onJump={jumpToSource} onClearSelection={() => setSelection(null)} active={panel === 'tutor'}
                onPractice={selection ? () => void op.run(practice) : undefined} practiceBusy={op.busy} />
            </div>
          )}
          {quiz && (
            <div className="studio-reader-evidence-slot" hidden={panel !== 'practice'}>
              <Link className="studio-outlink mb-3" href={scopedUrl(`/objects/${quiz.record}`, workspace)}>{t('Open saved practice')}</Link>
              <StudyQuizViewer key={quiz.record} questions={quiz.data.questions} sessionId={quiz.data.session_id} turnId={quiz.data.turn_id} />
            </div>
          )}
          {panel === 'review' && canTutor && reference && <div className="studio-reader-tutor-slot">
            <PaperReviewPanel workspace={workspace} target={{ kind: 'edition', reference }} initialReviewId={initialReview} onJump={(source, targetPage, quote) => {
              if (source.id === edition?.id && source.revision === edition.revision && source.locator?.blob_hash === hash) jumpToSource(targetPage, hash, quote)
            }} />
          </div>}
          <div className="studio-reader-evidence-slot" hidden={panel === 'practice' || panel === 'review' || (panel === 'tutor' && canTutor && reference != null)}>
          <span className="studio-eyebrow">{t('READ · THINK · CONNECT')}</span>
          <h2 style={{ marginTop: 15 }}>{selection ? t('把这一处，留下来') : t('读到哪里，想到哪里')}</h2>
          <p className="studio-note">
            {t('选中原文，可以解释、追问或保存为证据。每条证据都固定到此版本、原件与物理页。')}
          </p>
          {saved && (
            <p className="studio-note" role="status">
              {saved}
            </p>
          )}
          {selection ? (
            <>
              <blockquote>{selection.quote}</blockquote>
              <div className="studio-actions" style={{ marginTop: 18 }}>
                <Action secondary disabled={!canTutor} onClick={() => ask(t('请解释这一选区，必要时补充先修概念。'))}>
                  <MessageCircle size={14} />
                  {t('解释')}
                </Action>
                <Action
                  secondary
                  disabled={!canTutor}
                  onClick={() =>
                    ask(t('先给我一个提示，带我推导这段内容的下一步，暂不展示完整答案。'))
                  }
                >
                  {t('分步提示')}
                </Action>
                <Action
                  secondary
                  disabled={!canTutor}
                  onClick={() => ask(t('围绕这段材料给出一个反例，明确原结论的假设和适用边界。'))}
                >
                  {t('给反例')}
                </Action>
                <Action secondary disabled={op.busy || !canTutor} onClick={() => op.run(practice)}>
                  {t('现在测我')}
                </Action>
              </div>
              <Field label={t("证据名称")}>
                <input
                  value={label}
                  onChange={(e) => setLabel(e.target.value)}
                  placeholder={t('例如 Table 2：未见物体上的 F-score')}
                />
              </Field>
              <Field label={t("选区类型")}>
                <select value={kind} onChange={(e) => setKind(e.target.value)}>
                  <option value="quote">{t('方法或文字')}</option>
                  <option value="table">{t('表格')}</option>
                  <option value="figure">{t("图")}</option>
                  <option value="formula">{t('公式')}</option>
                </select>
              </Field>
              <Field label={t("自己的理解或疑问")}>
                <textarea
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder={t('作者报告了什么？有哪些条件仍需核实？')}
                />
              </Field>
              <label className="studio-note" style={{ display: 'flex', gap: 8 }}>
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(e) => setConfirmed(e.target.checked)}
                />
                {t('我已核对选区和原文位置')}
              </label>
              <Action disabled={op.busy} onClick={() => void op.run(save)}>
                <BookmarkPlus size={15} />
                {op.busy ? t('保存中…') : t('保存为项目证据')}
              </Action>
            </>
          ) : (
            <div className="studio-card-list">
              {evidence
                .filter((e) => e.data.blob_hash === hash)
                .map((e) => (
                  <button
                    key={e.id}
                    className="studio-card"
                    style={{ textAlign: 'left', padding: 14 }}
                    onClick={() =>
                      setJump({ locator: e.data.page, quote: e.data.quote, nonce: Date.now() })
                    }
                  >
                    <h3 style={{ fontSize: 12 }}>{e.data.label}</h3>
                    <span className="studio-badge">
                      {t('第 {{page}} 页', { page: e.data.page })} ·{' '}
                      {e.data.verification === 'confirmed' ? t('已核对') : t('待核对')}
                    </span>
                  </button>
                ))}
            </div>
          )}
          </div>
        </aside>
      </div>
    </section>
  )
}
