import { renderHook, act } from '@testing-library/react'
import { expect, it } from 'vitest'
import { useKeyedState } from '@/hooks/useKeyedState'
it('resets before a new resource is committed and ignores a late previous-resource update', () => {
  const hook = renderHook(({ key }) => useKeyedState(key, () => ({ text: '' })), {
    initialProps: { key: 'first' },
  })
  const oldSetter = hook.result.current[1]
  act(() => oldSetter({ text: 'first draft' }))
  expect(hook.result.current[0].text).toBe('first draft')
  hook.rerender({ key: 'second' })
  expect(hook.result.current[0].text).toBe('')
  act(() => oldSetter({ text: 'late first response' }))
  expect(hook.result.current[0].text).toBe('')
  act(() => hook.result.current[1]({ text: 'second draft' }))
  hook.rerender({ key: 'second' })
  expect(hook.result.current[0].text).toBe('second draft')
})
