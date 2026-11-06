'use client'
import { useState, useSyncExternalStore, type Dispatch, type SetStateAction } from 'react'

/** Browser-owned initial state with a deterministic server snapshot. The read
 * happens once after hydration, and subsequent edits notify this store. Use a
 * keyed component when the storage identity changes. */
export function useHydratedState<T>(
  read: () => T,
  serverValue: T,
): [T, Dispatch<SetStateAction<T>>] {
  const [store] = useState(() => {
    let initialized = false
    let value = serverValue
    const listeners = new Set<() => void>()
    const snapshot = () => {
      if (!initialized) {
        initialized = true
        try {
          value = read()
        } catch {
          value = serverValue
        }
      }
      return value
    }
    return {
      snapshot,
      server: () => serverValue,
      subscribe: (listener: () => void) => {
        listeners.add(listener)
        return () => {
          listeners.delete(listener)
        }
      },
      set: (next: SetStateAction<T>) => {
        const previous = snapshot()
        value = typeof next === 'function' ? (next as (previous: T) => T)(previous) : next
        if (!Object.is(previous, value)) listeners.forEach((listener) => listener())
      },
    }
  })
  return [useSyncExternalStore(store.subscribe, store.snapshot, store.server), store.set]
}
