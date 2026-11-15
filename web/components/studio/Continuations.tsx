'use client'
import { useTranslation } from 'react-i18next'
import { useEffect, useState, useSyncExternalStore } from 'react'
import Link from 'next/link'
import { ArrowUpRight, BookOpen, CheckCheck, PenLine } from 'lucide-react'
import { useRecoveryPrincipal } from '@/hooks/useRecoveryPrincipal'
import { manuscriptDraftEntries, migrateLocalManuscriptDrafts } from '@/lib/manuscript-draft'
import { studio } from '@/lib/studio-api'
import { scopedUrl } from '@/lib/workspace-scope'
import './studio.css'
interface Item {
  id: string
  kind: string
  title: string
  revision: number
  manuscript_id?: string
  path?: string
  source?: { id: string; revision: number; locator: { page: number; blob_hash: string } }
}
function subscribeDrafts(listener: () => void) {
  window.addEventListener('storage', listener)
  window.addEventListener('focus', listener)
  return () => {
    window.removeEventListener('storage', listener)
    window.removeEventListener('focus', listener)
  }
}
export default function Continuations({ workspace }: { workspace: string }) {
  const principal = useRecoveryPrincipal()
  if (!principal) return null
  return <ProjectContinuations key={JSON.stringify([principal, workspace])} workspace={workspace} principal={principal} />
}
function ProjectContinuations({ workspace, principal }: { workspace: string; principal: string }) {
  const { t } = useTranslation()

  const [items, setItems] = useState<Item[]>([]),
    [error, setError] = useState(''),
    [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const abort = new AbortController()
    migrateLocalManuscriptDrafts(principal, workspace)
    void studio<{ items: Item[] }>(workspace, '/continuations', {}, abort.signal)
      .then(r => {
        if (!abort.signal.aborted) {
          setItems(r.items)
          setError('')
        }
      })
      .catch(e => {
        if (!abort.signal.aborted) setError(e.message)
      })
    return () => abort.abort()
  }, [principal, workspace, attempt])
  const drafts = useSyncExternalStore(
    subscribeDrafts,
    () => JSON.stringify(manuscriptDraftEntries(principal, workspace)),
    () => '[]'
  )
  const conflicts: Item[] = []
  for (const { key, document, path, value } of JSON.parse(drafts) as ReturnType<typeof manuscriptDraftEntries>) {
    try {
      const draft = JSON.parse(value)
      if (draft.conflict) {
        conflicts.push({
          id: key,
          kind: 'draft_conflict',
          title: path,
          revision: 0,
          manuscript_id: document,
          path,
        })
      }
    } catch {}
  }
  if (!items.length && !error && !conflicts.length) return null
  return (
    <section className="studio-continuations">
      <header>
        <h2>{t('继续阅读与研究')}</h2>
      </header>
      {error && (
        <div className="studio-actions">
          <p className="studio-note" role="alert">{t('暂时无法读取科研续办：')}{error}</p>
          <button type="button" className="studio-button secondary" onClick={() => setAttempt(value => value + 1)}>{t('Retry')}</button>
        </div>
      )}
      <div className="studio-continuation-grid">
        {[...conflicts, ...items].map(item => {
          const href =
            item.kind === 'draft_conflict'
              ? `/studio/writing?manuscript=${item.manuscript_id}&path=${encodeURIComponent(item.path || '')}`
              : item.kind === 'resume' && item.source
                ? `/library/${encodeURIComponent(item.source.id)}?revision=${item.source.revision}&file=${item.source.locator.blob_hash}&page=${item.source.locator.page}`
                : `/objects/${item.id}?revision=${item.revision}`
          const Icon =
            item.kind === 'resume' ? BookOpen : item.kind === 'proposal' ? PenLine : CheckCheck
          return (
            <Link key={item.id} className="studio-continuation" href={scopedUrl(href, workspace)}>
              <Icon size={18} />
              <div>
                <span>
                  {
                    (
                      {
                        draft_conflict: t('处理稿件草稿冲突'),
                        remote_snapshot: t('核对待导入结果'),
                        resume: t('继续第 {{value1}} 页', { value1: item.source?.locator.page }),
                        evidence: t('核对原文证据'),
                        claim: t('核对研究主张'),
                        proposal: t('审阅稿件修改'),
                        activity: t('练习与反馈'),
                        run: t('核对实验记录'),
                      } as Record<string, string>
                    )[item.kind]
                  }
                </span>
                <strong>{item.title}</strong>
              </div>
              <ArrowUpRight size={15} />
            </Link>
          )
        })}
      </div>
    </section>
  )
}
