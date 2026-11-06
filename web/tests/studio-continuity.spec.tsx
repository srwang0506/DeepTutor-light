import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import ReviewProtocols from '@/components/studio/ReviewProtocols'
import PortableWorkspace from '@/components/studio/PortableWorkspace'
import LiveRunTimeline from '@/components/studio/LiveRunTimeline'

const api = vi.hoisted(() => ({ studio: vi.fn(), records: vi.fn(), post: vi.fn() }))
vi.mock('@/lib/studio-api', () => ({ ...api, studioUrl: (url: string) => url }))
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))
vi.mock('next/navigation', () => ({ usePathname: () => '/evidence' }))
vi.mock('next/link', () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}))

beforeEach(() => {
  api.studio.mockReset().mockResolvedValue([])
  api.records.mockReset().mockResolvedValue([])
  api.post.mockReset().mockResolvedValue({})
})

it('shows stale extraction status with links to its evidence', async () => {
  api.records.mockResolvedValue([{ id: 'protocol', data: { title: 'HOI review' } }])
  api.studio.mockResolvedValue({
    protocol: {
      id: 'protocol',
      data: {
        question: 'What is measured?',
        search_query: 'HOI',
        inclusion: ['HOI'],
        exclusion: ['No original'],
        extraction_fields: ['Metric'],
      },
    },
    counts: { included: 1 },
    items: [
      {
        title: 'Paper',
        edition: { id: 'paper', revision: 2, data: { attachments: [] } },
        state: 'included',
        stale: false,
        decisions: {},
        extractions: {
          Metric: {
            id: 'extraction',
            stale: true,
            data: { value: 'mAP', evidence_ids: ['anchor'] },
          },
        },
      },
    ],
  })
  render(<ReviewProtocols workspace="research" />)
  fireEvent.click(await screen.findByRole('button', { name: 'HOI review' }))
  expect(await screen.findByText(/来源已变，需重新复核/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '原文证据 1' })).toHaveAttribute(
    'href',
    expect.stringContaining('anchor'),
  )
})

it('requires a choice for every transfer conflict before applying', async () => {
  api.studio.mockImplementation(async (_workspace, url) =>
    url === '/portable/preview'
      ? {
          batch_id: 'batch',
          state: 'prepared',
          items: [{ resource: 'session/chat', state: 'conflict' }],
        }
      : [],
  )
  render(<PortableWorkspace workspace="target" />)
  fireEvent.change(screen.getByLabelText('传输口令（至少 12 个字符）'), {
    target: { value: 'a long test passphrase' },
  })
  fireEvent.change(screen.getByLabelText('接收另一台设备的项目包'), {
    target: { files: [new File(['fixture'], 'project.dtsync')] },
  })
  fireEvent.click(screen.getByRole('button', { name: '预览导入' }))
  const apply = await screen.findByRole('button', { name: '确认导入此工作区' })
  expect(apply).toBeDisabled()
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'keep' } })
  expect(apply).toBeEnabled()
  fireEvent.click(apply)
  await waitFor(() =>
    expect(api.post).toHaveBeenCalledWith('target', '/portable/apply', {
      batch_id: 'batch',
      choices: { 'session/chat': 'keep' },
    }),
  )
})

it('keeps a return to live action when an older event page is empty', async () => {
  api.studio
    .mockResolvedValueOnce({
      events: [{ event_id: 'e', type: 'run', _cursor: 4 }],
      cursor: '4',
      first_cursor: '4',
      has_more: true,
    })
    .mockResolvedValue({ events: [], cursor: '0', first_cursor: '0', has_more: false })
  render(<LiveRunTimeline workspace="research" runId="run" />)
  fireEvent.click(await screen.findByRole('button', { name: '更早事件' }))
  expect(await screen.findByRole('button', { name: '返回实时' })).toBeEnabled()
  await waitFor(() =>
    expect(api.studio).toHaveBeenLastCalledWith(
      'research',
      '/runs/run/events?after=latest&before=4',
    ),
  )
})
