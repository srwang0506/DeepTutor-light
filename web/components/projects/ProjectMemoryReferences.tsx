'use client'
import Link from 'next/link'
import { Layers3 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { StreamEvent } from '@/features/chat/model/protocol'

export default function ProjectMemoryReferences({ events }: { events: StreamEvent[] }) {
  const { i18n } = useTranslation(),
    zh = i18n.language.startsWith('zh')
  const sources = events.flatMap(event => {
    const value = event.metadata?.project_memory_sources
    if (!Array.isArray(value)) return []
    return value.filter(
      (s): s is { url: string; title: string; workspace_name: string; external: boolean } =>
        s &&
        typeof s.url === 'string' &&
        /^\/(chat\/|memory[/?]|kanban\?)/.test(s.url) &&
        typeof s.title === 'string'
    )
  })
  if (!sources.length) return null
  const unique = [...new Map(sources.map(s => [s.url + s.title, s])).values()]
  return (
    <details className="my-3 rounded-xl border border-[var(--border)] px-3 py-2 text-xs">
      <summary className="cursor-pointer text-[var(--muted-foreground)]">
        <Layers3 size={13} className="mr-2 inline-block" />
        {zh ? `参考了 ${unique.length} 条项目记忆` : `${unique.length} project memory references`}
      </summary>
      <div className="mt-2 flex flex-col gap-2">
        {unique.map(source => (
          <Link
            key={source.url + source.title}
            href={source.url}
            className="flex gap-2 rounded-md px-1 py-1 hover:bg-[var(--muted)]"
          >
            <span className="shrink-0 text-[var(--muted-foreground)]">
              {source.external ? source.workspace_name : zh ? '本项目' : 'This project'}
            </span>
            <span className="truncate">{source.title}</span>
          </Link>
        ))}
      </div>
    </details>
  )
}
