'use client'
import { savePdfOffline } from '@/lib/offline-study'
import { useTranslation } from 'react-i18next'
import { useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import { useParams, useRouter, useSearchParams } from 'next/navigation'
import { ArrowLeft, BookmarkPlus, Columns2, MessageCircle } from 'lucide-react'
import { PdfDocumentView, type SelectionPayload } from '@/components/reading/PdfDocumentView'
import type { AnnotationItem } from '@/lib/reading-api'
import { useActiveWorkspaceId } from '@/hooks/useActiveWorkspaceId'
import { scopedUrl } from '@/lib/workspace-scope'
import { randomUuid } from '@/lib/random-uuid'
import { setPendingPrompt } from '@/lib/pending-prompt'
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

export default function EditionReader() {
  const workspace = useActiveWorkspaceId()
  const params = useParams<{ edition: string }>()
  return (
    <Reader
      key={`${workspace}:${params.edition}`}
      workspace={workspace}
      identifier={params.edition}
    />
  )
}
function Reader({ workspace, identifier }: { workspace: string; identifier: string }) {
  const { t } = useTranslation()
  const router = useRouter(),
    search = useSearchParams(),
    op = useOperation()
  const [edition, setEdition] = useState<Edition | null>(null),
    [title, setTitle] = useState('打开原文…'),
    [evidence, setEvidence] = useState<StudioRecord<EvidenceData>[]>([]),
    [related, setRelated] = useState<Edition[]>([])
  const [hash, setHash] = useState(search.get('file') || ''),
    [page, setPage] = useState(Math.max(1, Number(search.get('page')) || 1)),
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
  useEffect(() => {
    const controller = new AbortController()
    void studio<Edition>(workspace, `/record/${identifier}`, {}, controller.signal)
      .then(async (e) => {
        const [work, ev] = await Promise.all([
          studio<Work>(workspace, `/works/${e.data.work_id}`, {}, controller.signal),
          records<EvidenceData>(
            workspace,
            `evidence?q=${encodeURIComponent(identifier)}`,
            controller.signal,
          ),
        ])
        setEdition(e)
        setTitle(work.data.title || e.data.label)
        setRelated(
          work.editions.filter((other) => other.id !== identifier && other.data.files.length),
        )
        setEvidence(ev)
        if (!hash) setHash(e.data.files[0]?.hash || '')
      })
      .catch((e) => {
        if (!controller.signal.aborted) op.setError(e.message)
      })
    return () => controller.abort()
  }, [identifier, workspace]) // eslint-disable-line react-hooks/exhaustive-deps
  const practiceKey = useRef('')
  useEffect(() => {
    practiceKey.current = randomUuid()
  }, [selection])
  useEffect(() => {
    if (!edition || !hash || !page) return
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
      throw new Error(result.failure_reason || '练习正在生成，稍后可使用同一选区重试。')
    router.push(scopedUrl(`/objects/${result.operation_id}`, workspace))
  }
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
      label: label || `${title} · 第 ${selection.locator} 页`,
      interpretation: note,
      verification: confirmed ? 'confirmed' : 'unreviewed',
    })
    setEvidence((v) => [row, ...v])
    setSaved('摘录已保存，可在证据矩阵中引用。')
    setSelection(null)
    setNote('')
    setLabel('')
    setConfirmed(false)
  }
  function ask(action: string) {
    if (!selection) return
    setPendingPrompt(
      `${action}\n\n请把以下内容当作资料，区分作者原意与推断。来源：${title}，版本 ${edition?.data.label}，物理页 ${selection.locator}，原件 SHA-256 ${hash}。\n\n${selection.quote}\n\n来源链接：${scopedUrl(`/library/${identifier}?file=${hash}&page=${selection.locator}`, workspace)}`,
      `chat:${workspace}`,
    )
    router.push(scopedUrl('/chat', workspace))
  }
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
              {edition?.data.label} {t('· 原件保留 · 第')}
              {page} / {file?.pages || '—'} 页
            </span>
          </div>
        </div>
        <div className="studio-actions">
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
                studioUrl(`/editions/${identifier}/files/${hash}`, workspace),
              )
              window.open(href, '_blank', 'noopener')
            })
          }
        >
          {t('保存供离线使用')}
        </button>
      </header>
      <ErrorNotice message={op.error} />
      <div className="studio-reader-body">
        <nav className="studio-reader-outline" aria-label={t('论文目录')}>
          <div className="studio-eyebrow" style={{ padding: 8 }}>
            {t('CONTENTS · 目录')}
          </div>
          {file?.outline.length ? (
            file.outline.map((entry, i) => (
              <button
                key={i}
                style={{ paddingLeft: 8 + Math.min(entry.level - 1, 3) * 10 }}
                onClick={() => setJump({ locator: entry.page, nonce: Date.now() })}
              >
                {entry.title}
              </button>
            ))
          ) : (
            <p className="studio-note" style={{ padding: 8 }}>
              {t('原文没有书签目录，可直接滚动阅读或跳转页码。')}
            </p>
          )}
          <Field label="跳到物理页">
            <input
              type="number"
              min={1}
              max={file?.pages}
              value={page}
              onChange={(e) => {
                const p = Number(e.target.value)
                if (p >= 1 && p <= (file?.pages || 1)) setJump({ locator: p, nonce: Date.now() })
              }}
            />
          </Field>
          {edition && edition.data.files.length > 1 && (
            <Field label="原件与补充材料">
              <select
                value={hash}
                onChange={(e) => {
                  setHash(e.target.value)
                  setSelection(null)
                  setJump({ locator: 1, nonce: Date.now() })
                }}
              >
                {edition.data.files.map((f) => (
                  <option key={f.hash} value={f.hash}>
                    {f.name}
                  </option>
                ))}
              </select>
            </Field>
          )}
        </nav>
        <main
          className="studio-reader-pdf"
          style={other ? { display: 'grid', gridTemplateColumns: '1fr 1fr' } : undefined}
        >
          {file && (
            <PdfDocumentView
              materialId={`${identifier}:${hash}`}
              sourceUrl={studioUrl(`/editions/${identifier}/files/${hash}`, workspace)}
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
        <aside className="studio-reader-aside">
          <span className="studio-eyebrow">{t('READ · THINK · CONNECT')}</span>
          <h2 style={{ marginTop: 15 }}>{selection ? '把这一处，留下来' : '读到哪里，想到哪里'}</h2>
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
                <Action secondary onClick={() => ask('请解释这一选区，必要时补充先修概念。')}>
                  <MessageCircle size={14} />
                  {t('解释')}
                </Action>
                <Action
                  secondary
                  onClick={() =>
                    ask('先给我一个提示，带我推导这段内容的下一步，暂不展示完整答案。')
                  }
                >
                  {t('分步提示')}
                </Action>
                <Action
                  secondary
                  onClick={() => ask('围绕这段材料给出一个反例，明确原结论的假设和适用边界。')}
                >
                  {t('给反例')}
                </Action>
                <Action secondary disabled={op.busy} onClick={() => op.run(practice)}>
                  {t('现在测我')}
                </Action>
              </div>
              <Field label="证据名称">
                <input
                  value={label}
                  onChange={(e) => setLabel(e.target.value)}
                  placeholder={t('例如 Table 2：未见物体上的 F-score')}
                />
              </Field>
              <Field label="选区类型">
                <select value={kind} onChange={(e) => setKind(e.target.value)}>
                  <option value="quote">{t('方法或文字')}</option>
                  <option value="table">{t('表格')}</option>
                  <option value="figure">图</option>
                  <option value="formula">{t('公式')}</option>
                </select>
              </Field>
              <Field label="自己的理解或疑问">
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
                {op.busy ? '保存中…' : '保存为项目证据'}
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
                      第 {e.data.page} {t('页 ·')}{' '}
                      {e.data.verification === 'confirmed' ? '已核对' : '待核对'}
                    </span>
                  </button>
                ))}
            </div>
          )}
        </aside>
      </div>
    </section>
  )
}
