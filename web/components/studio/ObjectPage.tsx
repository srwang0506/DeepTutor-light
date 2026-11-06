'use client'
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useParams, useSearchParams } from 'next/navigation'
import { useActiveWorkspaceId } from '@/hooks/useActiveWorkspaceId'
import { scopedUrl } from '@/lib/workspace-scope'
import { studio, type StudioRecord } from '@/lib/studio-api'
import { StudioShell, ErrorNotice } from './StudioShell'
import StudyQuizViewer from '@/components/quiz/StudyQuizViewer'
import type { QuizQuestion } from '@/lib/quiz-types'

const kinds: Record<string, string> = {
  activity: '原文练习',
  evidence: '原文证据',
  observation: '观测记录',
  claim: '研究主张',
  run: '实验记录',
  proposal: '稿件修改',
  artifact: '研究图表',
  remote_snapshot: '读取快照',
}
const states: Record<string, string> = {
  committed: '已保存',
  prepared: '待完成',
  running: '进行中',
  failed: '需要重试',
  draft: '待核实',
  confirmed: '已核对',
  disputed: '存在反证',
  reported: '已记录数值',
  pending: '待审阅',
  accepted: '已接受',
  rejected: '已拒绝',
  succeeded: '记录为成功',
  planned: '尚未运行',
  static_only: '仅静态检查',
}

type Data = {
  source_ref?: { id: string; revision: number }
  value?: number
  protocol?: Record<string, unknown>
  origin?: string
  title?: string
  label?: string
  quote?: string
  note?: string
  interpretation?: string
  status?: string
  state?: string
  path?: string
  manuscript_id?: string
  edition_id?: string
  blob_hash?: string
  page?: number
  source_id?: string
  source?: { id: string; revision: number; locator: { page?: number; blob_hash?: string } }
  session_id?: string
  turn_id?: string
  questions?: QuizQuestion[]
  edges?: {
    source_id: string
    source_revision: number
    role: string
    qualifiers: string
    review: string
  }[]
}
export default function ObjectPage() {
  const workspace = useActiveWorkspaceId(),
    { id } = useParams<{ id: string }>(),
    query = useSearchParams()
  const revision = query.get('revision')
  return (
    <ObjectView
      key={`${workspace}:${id}:${revision}`}
      workspace={workspace}
      id={id}
      revision={revision}
    />
  )
}
function ObjectView({
  workspace,
  id,
  revision,
}: {
  workspace: string
  id: string
  revision: string | null
}) {
  const [row, setRow] = useState<(StudioRecord<Data> & { latest_revision: number }) | null>(null),
    [error, setError] = useState('')
  useEffect(() => {
    const abort = new AbortController()
    void studio<StudioRecord<Data> & { latest_revision: number }>(
      workspace,
      `/record/${id}${revision ? `?revision=${encodeURIComponent(revision)}` : ''}`,
      {},
      abort.signal
    )
      .then(setRow)
      .catch(e => {
        if (!abort.signal.aborted) setError(e.message)
      })
    return () => abort.abort()
  }, [workspace, id, revision])
  const d = row?.data
  const original = d?.edition_id
    ? { id: d.edition_id, locator: { blob_hash: d.blob_hash, page: d.page } }
    : d?.source
  return (
    <StudioShell
      workspace={workspace}
      active={
        row?.kind === 'activity'
          ? 'library'
          : row?.kind === 'run'
            ? 'experiments'
            : row?.kind === 'proposal'
              ? 'writing'
              : 'evidence'
      }
      title={d?.title || d?.label || '来源与记录'}
      description="保留当时的版本、来源和核对状态。来源更新时，由你决定何时采用新版本。"
    >
      <ErrorNotice message={error} />
      {!row && !error && (
        <p role="status" className="studio-note">
          正在读取冻结版本…
        </p>
      )}
      {row && (
        <>
          <div className="studio-badges">
            <span className="studio-badge">
              {kinds[row.kind] || '项目记录'} · 版本 {row.revision}
            </span>
            <span className="studio-badge">
              {states[d?.status || d?.state || ''] || '已保存记录'}
            </span>
          </div>
          {row.revision !== row.latest_revision && (
            <p className="studio-note">
              这是被引用的旧版本。
              <Link href={scopedUrl(`/objects/${row.id}`, workspace)}>
                查看最新版本 {row.latest_revision} ↗
              </Link>
            </p>
          )}
          <div className="studio-actions" style={{ margin: '20px 0' }}>
            {original && (
              <Link
                className="studio-outlink"
                href={scopedUrl(
                  `/library/${original.id}?file=${original.locator.blob_hash || ''}&page=${original.locator.page || 1}`,
                  workspace
                )}
              >
                回到原件 · 第 {original.locator.page || 1} 页 ↗
              </Link>
            )}
            {d?.manuscript_id && (
              <Link
                className="studio-outlink"
                href={scopedUrl(
                  `/studio/writing?manuscript=${d.manuscript_id}&path=${encodeURIComponent(d.path || '')}`,
                  workspace
                )}
              >
                打开对应稿件 ↗
              </Link>
            )}
            {d?.source_id && (
              <Link
                className="studio-outlink"
                href={scopedUrl(
                  `/objects/${d.source_id}${d.source_ref ? `?revision=${d.source_ref.revision}` : ''}`,
                  workspace
                )}
              >
                查看底层来源 ↗
              </Link>
            )}
          </div>
          {typeof d?.value === 'number' && (
            <section className="studio-panel">
              <span className="studio-eyebrow">OBSERVATION · 原始观测</span>
              <h2 style={{ fontSize: 36, margin: '12px 0' }}>
                {d.value} <small style={{ fontSize: 16 }}>{String(d.protocol?.unit || '')}</small>
              </h2>
              <p className="studio-note">
                {d.origin === 'reported'
                  ? '作者报告'
                  : d.origin === 'executed'
                    ? '运行记录'
                    : '待验证数据'}{' '}
                · {String(d.protocol?.metric || '指标待核对')}
              </p>
              <div className="studio-form-grid">
                {Object.entries(d.protocol || {})
                  .filter(
                    ([key]) => !['extensions', 'not_applicable', 'field_states'].includes(key)
                  )
                  .map(([key, value]) => (
                    <div key={key}>
                      <span className="studio-eyebrow">{key}</span>
                      <p>{String(value || '未知')}</p>
                    </div>
                  ))}
              </div>
            </section>
          )}
          {d?.quote && <blockquote className="studio-passage-quote">{d.quote}</blockquote>}
          {(d?.interpretation || d?.note) && (
            <p style={{ whiteSpace: 'pre-wrap' }}>{d.interpretation || d.note}</p>
          )}
          {d?.edges?.map(edge => (
            <article className="studio-card" key={edge.source_id}>
              <span className="studio-badge">
                {
                  (
                    {
                      supports: '支持',
                      contradicts: '反对',
                      qualifies: '限定',
                      context: '背景',
                    } as Record<string, string>
                  )[edge.role]
                }{' '}
                · {edge.review === 'confirmed' ? '关系已核对' : '关系待核对'}
              </span>
              <p>{edge.qualifiers}</p>
              <Link
                className="studio-outlink"
                href={scopedUrl(
                  `/objects/${edge.source_id}?revision=${edge.source_revision}`,
                  workspace
                )}
              >
                查看证据版本 {edge.source_revision} ↗
              </Link>
            </article>
          ))}
          {row.kind === 'activity' && d?.session_id && d.turn_id && d.questions && (
            <StudyQuizViewer questions={d.questions} sessionId={d.session_id} turnId={d.turn_id} />
          )}
          <details className="studio-panel" style={{ marginTop: 24 }}>
            <summary>完整记录与复核细节</summary>
            <pre className="studio-diff">{JSON.stringify(d, null, 2)}</pre>
          </details>
        </>
      )}
    </StudioShell>
  )
}
