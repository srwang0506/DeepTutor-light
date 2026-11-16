import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { ChatStateAdapterProvider, useChatStateAdapter } from '@/features/chat/ChatStateAdapter'

const mocks = vi.hoisted(() => ({ requests: [] as Record<string, unknown>[] }))
vi.mock('@/features/chat/transport/UnifiedTurnClient', () => ({
  UnifiedTurnClient: class {
    connected = true
    connect() {}
    disconnect() {}
    setResumeState() {}
    send(message: Record<string, unknown>) { mocks.requests.push(message) }
  },
}))

function Harness() {
  const { state, sendMessage, setResourceSelection } = useChatStateAdapter()
  const snapshot = state.messages.find(message => message.role === 'user')?.requestSnapshot
  return <>
    <button onClick={() => sendMessage('Review this paper', [], undefined, undefined, undefined, {
      resourceSelection: { skills: ['best-paper-review'], mcp: [] },
    })}>Review</button>
    <button onClick={() => {
      setResourceSelection({ skills: ['another-skill'], mcp: ['another-server'] })
      if (snapshot) sendMessage('Review this paper', [], undefined, undefined, undefined, {
        requestSnapshotOverride: snapshot,
        resourceSelection: { skills: ['unwanted-override'], mcp: ['unwanted-server'] },
      })
    }}>Replay</button>
    <output data-testid="snapshot">{JSON.stringify(snapshot?.resourceSelection)}</output>
  </>
}

beforeEach(() => { localStorage.clear(); mocks.requests.length = 0 })

it('sends explicit resources without a state-setting race and preserves them in an exact replay', () => {
  render(<ChatStateAdapterProvider><Harness /></ChatStateAdapterProvider>)
  fireEvent.click(screen.getByText('Review'))
  expect(screen.getByTestId('snapshot')).toHaveTextContent('best-paper-review')
  fireEvent.click(screen.getByText('Replay'))
  const starts = mocks.requests.filter(request => request.type === 'start_turn')
  expect(starts).toHaveLength(2)
  for (const request of starts) {
    expect(request.skills).toEqual(['best-paper-review'])
    expect(request.mcp).toEqual([])
  }
})
