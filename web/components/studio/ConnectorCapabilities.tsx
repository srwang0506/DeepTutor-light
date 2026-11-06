'use client'

import { useEffect, useState } from 'react'
import { studio } from '@/lib/studio-api'
import { ErrorNotice } from './StudioShell'

interface Connector {
  id: string
  name: string
  mode: string
  authorization: string
  formats: string[]
  quota: string
  degraded: string
  conflicts: string
  deletion: string
}
const modes: Record<string, string> = {
  native: '原生功能',
  authorized_read: '授权读取',
  file_exchange: '文件交换',
  external_open: '打开外部应用',
  reviewable_write: '审核后写入',
}

export default function ConnectorCapabilities({ workspace }: { workspace: string }) {
  const [items, setItems] = useState<Connector[]>([])
  const [error, setError] = useState('')
  useEffect(() => {
    const abort = new AbortController()
    studio<{ connectors: Connector[] }>(workspace, '/connectors', {}, abort.signal)
      .then(result => setItems(result.connectors))
      .catch(e => {
        if (!abort.signal.aborted) setError(e.message)
      })
    return () => abort.abort()
  }, [workspace])
  return (
    <details className="studio-panel" style={{ marginTop: 24 }}>
      <summary>应用接入与能力范围</summary>
      <p className="studio-note">查看每种连接实际能做的事、授权方式和当前限制。</p>
      <ErrorNotice message={error} />
      <div className="studio-cards">
        {items.map(item => (
          <article className="studio-panel" key={item.id}>
            <span className="studio-badge">{modes[item.mode] || item.mode}</span>
            <h3>{item.name}</h3>
            <p>{item.authorization}</p>
            <p className="studio-note">
              {item.formats.join(' · ')} · {item.quota}
            </p>
            <p className="studio-note">
              {item.conflicts}；{item.deletion}。{item.degraded}。
            </p>
          </article>
        ))}
      </div>
    </details>
  )
}
