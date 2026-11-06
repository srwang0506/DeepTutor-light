'use client'
import { useTranslation } from 'react-i18next'
import { useEffect, useState, useSyncExternalStore } from 'react'
import Link from 'next/link'
import { ArrowUpRight, BookOpen, CheckCheck, PenLine } from 'lucide-react'
import { browserStorage } from '@/shared/storage'
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
  source?: { id: string; locator: { page: number; blob_hash: string } }
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
  const { t } = useTranslation()

  const [items, setItems] = useState<Item[]>([]),
    [error, setError] = useState('')
  useEffect(() => {
    const abort = new AbortController()
    void studio<{ items: Item[] }>(workspace, '/continuations', {}, abort.signal)
      .then((r) => setItems(r.items))
      .catch((e) => {
        if (!abort.signal.aborted) setError(e.message)
      })
    return () => abort.abort()
  }, [workspace])
  const prefix = `studio-draft:${workspace}:`
  const drafts = useSyncExternalStore(
    subscribeDrafts,
    () => JSON.stringify(browserStorage.listRaw('session', prefix)),
    () => '[]',
  )
  const conflicts: Item[] = []
  for (const [key, value] of JSON.parse(drafts) as [string, string][]) {
    try {
      const draft = JSON.parse(value)
      if (draft.conflict) {
        const [manuscript, ...path] = key.slice(prefix.length).split(':')
        conflicts.push({
          id: key,
          kind: 'draft_conflict',
          title: path.join(':'),
          revision: 0,
          manuscript_id: manuscript,
          path: path.join(':'),
        })
      }
    } catch {}
  }
  if (!items.length && !error && !conflicts.length) return null
  return (
    <section className="studio-continuations">
      <header>
        <span className="studio-eyebrow">{t('CONTINUE · 继续推进')}</span>
        <h2>{t('留下的线索，接着往前。')}</h2>
      </header>
      {error && (
        <p className="studio-note">
          {t('暂时无法读取科研续办：')}
          {error}
        </p>
      )}
      <div className="studio-continuation-grid">
        {[...conflicts, ...items].map((item) => {
          const href =
            item.kind === 'draft_conflict'
              ? `/studio/writing?manuscript=${item.manuscript_id}&path=${encodeURIComponent(item.path || '')}`
              : item.kind === 'resume' && item.source
                ? `/library/${item.source.id}?file=${item.source.locator.blob_hash}&page=${item.source.locator.page}`
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
                        draft_conflict: '处理稿件草稿冲突',
                        remote_snapshot: '核对待导入结果',
                        resume: `继续第 ${item.source?.locator.page} 页`,
                        evidence: '核对原文证据',
                        claim: '核对研究主张',
                        proposal: '审阅稿件修改',
                        activity: '练习与反馈',
                        run: '核对实验记录',
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
