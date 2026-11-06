import { act, renderHook } from '@testing-library/react'
import { renderToString } from 'react-dom/server'
import { expect, it, vi } from 'vitest'
import { useHydratedState } from '@/hooks/useHydratedState'

it('uses a deterministic server snapshot and reads browser state once', () => {
  const read = vi.fn(() => ({ value: 'saved' }))
  function Server() {
    const [state] = useHydratedState(read, { value: 'default' })
    return <p>{state.value}</p>
  }
  expect(renderToString(<Server />)).toContain('default')
  expect(read).not.toHaveBeenCalled()
  const hook = renderHook(() => useHydratedState(read, { value: 'default' }))
  expect(hook.result.current[0].value).toBe('saved')
  act(() => hook.result.current[1]((previous) => ({ value: previous.value + ' edit' })))
  hook.rerender()
  expect(hook.result.current[0].value).toBe('saved edit')
  expect(read).toHaveBeenCalledTimes(1)
})

it('keeps editing usable when optional browser storage fails', () => {
  const hook = renderHook(() =>
    useHydratedState<boolean>(() => {
      throw new Error('Storage disabled')
    }, false),
  )
  expect(hook.result.current[0]).toBe(false)
  act(() => hook.result.current[1](true))
  expect(hook.result.current[0]).toBe(true)
})
