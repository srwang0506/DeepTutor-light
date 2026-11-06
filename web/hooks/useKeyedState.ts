'use client'
import { useCallback, useState, type Dispatch, type SetStateAction } from 'react'

/** Reset an editable value before committing a new resource's UI. Keys should
 * be stable identities (a document ID, URL or version), never a fresh object. */
export function useKeyedState<T>(
  key: unknown,
  initial: T | (() => T),
): [T, Dispatch<SetStateAction<T>>] {
  const initialize = () => (typeof initial === 'function' ? (initial as () => T)() : initial)
  const [cell, setCell] = useState(() => ({ key, value: initialize() }))
  let value = cell.value
  if (!Object.is(cell.key, key)) {
    value = initialize()
    setCell({ key, value })
  }
  const setValue = useCallback<Dispatch<SetStateAction<T>>>(
    (next) => {
      setCell((previous) => {
        if (!Object.is(previous.key, key)) return previous
        const value = typeof next === 'function' ? (next as (old: T) => T)(previous.value) : next
        return Object.is(value, previous.value) ? previous : { ...previous, value }
      })
    },
    [key],
  )
  return [value, setValue]
}
