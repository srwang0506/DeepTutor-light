import { beforeEach, describe, expect, it, vi } from 'vitest'
vi.mock('@/lib/api', () => ({ apiFetch: vi.fn(), apiUrl: (s: string) => s }))
import { apiFetch } from '@/lib/api'
import { studio, StudioRequestError } from '@/lib/studio-api'
describe('Studio command transport', () => {
  beforeEach(() => vi.mocked(apiFetch).mockReset())
  it('preserves project scope and JSON content type for PATCH and PUT commands', async () => {
    vi.mocked(apiFetch).mockResolvedValue(new Response('{"revision":2}', { status: 200 }))
    await studio('project-a', '/writing/preferences', { method: 'PUT', body: JSON.stringify({ allow_cloud: false, expected_revision: 1 }) })
    const [url, init] = vi.mocked(apiFetch).mock.calls[0]
    expect(String(url)).toContain('dt_workspace=project-a')
    expect(new Headers(init?.headers).get('Content-Type')).toBe('application/json')
    expect(init?.method).toBe('PUT')
  })
  it('lets the browser choose multipart boundaries', async () => {
    vi.mocked(apiFetch).mockResolvedValue(new Response('{}', { status: 200 }))
    await studio('', '/manuscripts/import', { method: 'POST', body: new FormData() })
    expect(new Headers(vi.mocked(apiFetch).mock.calls[0][1]?.headers).has('Content-Type')).toBe(false)
  })
  it('keeps non-JSON transport failures translatable and retains their status', async () => {
    vi.mocked(apiFetch).mockResolvedValue(new Response('<h1>Unavailable</h1>', { status: 503 }))
    await expect(studio('', '/writing/preferences')).rejects.toMatchObject({
      message: 'Request failed. Check your input and try again.',
      status: 503,
    })
    await expect(studio('', '/writing/preferences')).rejects.toBeInstanceOf(StudioRequestError)
  })
})
