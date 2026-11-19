'use client'

import { useSyncExternalStore } from 'react'
import { apiFetch, apiUrl } from '@/lib/api'

export interface ModuleStatus {
  id: string
  installed: boolean
  enabled: boolean
  ready: boolean
  navigation: string[]
  issues: string[]
}

export function moduleRouteAvailable(path: string, modules: ModuleStatus[]): boolean {
  const owners = modules.filter(module => module.navigation.some(
    prefix => path === prefix || path.startsWith(`${prefix}/`)
  ))
  return owners.length === 0 || owners.every(module => module.installed && module.enabled)
}

// One deployment inventory request serves the sidebar, route gate and runtimes.
// A focus/retry refresh observes a server restarted with a different selection.
type Inventory = { modules: ModuleStatus[] | null; error: boolean }
const empty: Inventory = { modules: null, error: false }
let inventory = empty
let pending: Promise<void> | null = null
const listeners = new Set<() => void>()

function publish(next: Inventory) {
  inventory = next
  for (const notify of listeners) notify()
}
function reload() {
  if (pending) return pending
  if (inventory.error) publish({ ...inventory, error: false })
  pending = apiFetch(apiUrl('/api/modules'))
    .then(async response => {
      if (!response.ok) throw new Error('Module inventory unavailable')
      const result = await response.json() as { modules: ModuleStatus[] }
      publish({ modules: result.modules, error: false })
    })
    .catch(() => { if (!inventory.modules) publish({ modules: null, error: true }) })
    .finally(() => { pending = null })
  return pending
}
function subscribe(notify: () => void) {
  listeners.add(notify)
  if (listeners.size === 1) {
    if (!inventory.modules) void reload()
    window.addEventListener('focus', reload)
  }
  return () => {
    listeners.delete(notify)
    if (!listeners.size) window.removeEventListener('focus', reload)
  }
}
export function useModules() {
  const { modules, error } = useSyncExternalStore(subscribe, () => inventory, () => empty)
  return { modules, error, reload, available: (path: string) => modules !== null && moduleRouteAvailable(path, modules) }
}
