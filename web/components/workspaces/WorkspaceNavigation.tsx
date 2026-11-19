'use client'

import { Suspense, useEffect } from 'react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { listWorkspaces } from '@/lib/workspaces-api'
import {
  activeWorkspaceId,
  registerWorkspaceNavigator,
  rememberWorkspaceId,
  scopedUrl,
  selectWorkspace,
  workspaceIdOf,
} from '@/lib/workspace-scope'

/**
 * Write the tab's workspace into any URL that arrived without one, so the
 * address can be copied and every query-reading surface sees the same scope.
 * A remembered workspace that no longer exists falls back to General once.
 */
function ScopeCanonicalizer() {
  const pathname = usePathname()
  const query = useSearchParams()
  const named = workspaceIdOf(query)
  useEffect(() => {
    if (named !== null) {
      rememberWorkspaceId(named)
      return
    }
    // The tab already renders this scope. Naming it in the current history
    // entry should not fetch the same page again or interrupt a new draft.
    const { pathname: current, search, hash } = window.location
    window.history.replaceState(null, '', scopedUrl(`${current}${search}${hash}`))
  }, [named, pathname])
  useEffect(() => {
    const remembered = activeWorkspaceId()
    if (!remembered) return
    let alive = true
    listWorkspaces()
      .then(rows => {
        if (!alive || rows.some(row => row.workspace_id === remembered && row.kind === 'workspace')) return
        rememberWorkspaceId('')
        void selectWorkspace('', `/${window.location.pathname.split('/')[1] || 'chat'}`)
      })
      .catch(() => undefined)
    return () => { alive = false }
  }, [])
  return null
}

/** Cross-store navigation disposes client runtimes. Only a link that names a workspace leaves this one. */
export function WorkspaceNavigation() {
  const router = useRouter()
  useEffect(() => registerWorkspaceNavigator(href => {
    const current = new URL(window.location.href)
    const next = new URL(href, current)
    const otherParams = (url: URL) => {
      const params = new URLSearchParams(url.search)
      params.delete('dt_workspace')
      params.delete('workspace')
      params.sort()
      return params.toString()
    }
    if (current.pathname === next.pathname && otherParams(current) === otherParams(next)) {
      // selectWorkspace has already flushed drafts. Next's native history
      // integration rekeys the scoped runtime without refetching the same route.
      window.history.pushState(null, '', href)
    } else {
      router.push(href)
    }
  }), [router])
  useEffect(() => {
    const follow = (event: MouseEvent) => {
      const anchor = (event.target as Element).closest?.('a[href]') as HTMLAnchorElement | null
      if (!anchor || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey ||
          event.button !== 0 || anchor.target === '_blank' || anchor.hasAttribute('download')) return
      const url = new URL(anchor.href)
      if (url.origin !== window.location.origin) return
      const target = workspaceIdOf(url.searchParams)
      if (target !== null && target !== activeWorkspaceId()) {
        event.preventDefault()
        event.stopImmediatePropagation()
        void selectWorkspace(target, url.toString())
      }
    }
    document.addEventListener('click', follow, true)
    return () => document.removeEventListener('click', follow, true)
  }, [])
  return (
    <Suspense>
      <ScopeCanonicalizer />
    </Suspense>
  )
}
