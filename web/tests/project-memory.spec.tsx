import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ProjectEditor from '@/components/projects/ProjectEditor'
import ProjectMemoryReferences from '@/components/projects/ProjectMemoryReferences'
import { projectErrorMessage } from '@/components/projects/project-feedback'
import { ensureLanguage, initI18n } from '@/i18n/init'
import type { ChatWorkspaceRegistration } from '@/lib/workspaces-api'
import type { StreamEvent } from '@/features/chat/model/protocol'

const mocks = vi.hoisted(() => ({ save: vi.fn(), saved: vi.fn(), close: vi.fn() }))
vi.mock('@/lib/workspaces-api', async original => ({
  ...(await original<object>()),
  saveWorkspace: mocks.save,
}))
const i18n = initI18n()
const row = (id: string, extra = {}): ChatWorkspaceRegistration => ({
  workspace_id: id,
  kind: 'workspace',
  display_name: id,
  path: '/unused',
  archived: false,
  follows_root: true,
  created_at: '',
  status: 'ready',
  error: '',
  memory_policy: { sources: [], discoverable: true },
  ...extra,
})
const projects = [
  row('general-id', { kind: 'general' }),
  row('DSA'),
  row('HOI'),
  row('Private', { memory_policy: { sources: [], discoverable: false } }),
  row('Archive', { archived: true }),
]
afterEach(cleanup)
beforeEach(async () => {
  await ensureLanguage('zh')
  await i18n.changeLanguage('zh')
  vi.clearAllMocks()
  mocks.save.mockResolvedValue(row('new'))
})
function editor(project?: ChatWorkspaceRegistration) {
  render(
    <ProjectEditor
      project={project}
      projects={projects}
      zh
      onClose={mocks.close}
      onSaved={mocks.saved}
    />
  )
}

it('starts with independent memory and submits only explicitly chosen source identities', async () => {
  editor()
  expect(screen.getByRole('button', { name: '创建项目' })).toBeDisabled()
  expect(screen.getByRole('checkbox', { name: /通用工作区/ })).not.toBeChecked()
  expect(screen.getByRole('checkbox', { name: /Private/ })).toBeDisabled()
  expect(screen.getByRole('checkbox', { name: /Archive/ })).toBeDisabled()
  fireEvent.change(screen.getByRole('textbox', { name: '项目名称' }), {
    target: { value: 'Research' },
  })
  fireEvent.click(screen.getByRole('checkbox', { name: /通用工作区/ }))
  fireEvent.click(screen.getByRole('checkbox', { name: /DSA/ }))
  fireEvent.click(screen.getByRole('button', { name: '创建项目' }))
  await waitFor(() => expect(mocks.saved).toHaveBeenCalled())
  expect(mocks.save).toHaveBeenCalledWith(
    expect.objectContaining({
      name: 'Research',
      memory_policy: { sources: ['', 'DSA'], discoverable: true },
    }),
    undefined
  )
})

it('retains a draft on save error and persists removing a source', async () => {
  editor(row('HOI', { memory_policy: { sources: ['DSA'], discoverable: false } }))
  expect(screen.queryByRole('checkbox', { name: /^HOI/ })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('checkbox', { name: /DSA/ }))
  mocks.save.mockRejectedValueOnce(new Error('Storage unavailable'))
  fireEvent.click(screen.getByRole('button', { name: '保存设置' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(
    '此项目操作暂未完成，请重新加载后重试。'
  )
  expect(screen.getByRole('alert')).not.toHaveTextContent('Storage unavailable')
  expect(screen.getByRole('textbox', { name: '项目名称' })).toHaveValue('HOI')
  expect(mocks.saved).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '保存设置' }))
  await waitFor(() => expect(mocks.saved).toHaveBeenCalled())
  expect(mocks.save).toHaveBeenLastCalledWith(
    expect.objectContaining({ memory_policy: { sources: [], discoverable: false } }),
    'HOI'
  )
})

it('keeps paused sources visible and removable without granting new access', () => {
  editor(row('HOI', { memory_policy: { sources: ['Archive'], discoverable: true } }))
  const source = screen.getByRole('checkbox', { name: /Archive/ })
  expect(source).toBeChecked()
  expect(source).toBeEnabled()
  fireEvent.click(source)
  expect(source).not.toBeChecked()
  expect(source).toBeDisabled()
})

it('shows durable source links without accepting an external navigation target', () => {
  const sources = [
    { url: '/chat/s?dt_workspace=a', title: 'BFS plan', workspace_name: 'DSA', external: true },
    { url: 'https://evil.example', title: 'Unsafe', workspace_name: 'X', external: true },
  ]
  render(
    <ProjectMemoryReferences
      events={
        [{ type: 'observation', timestamp: 1, source: 'project_memory', stage: '', content: '', metadata: { project_memory_sources: sources } }] as StreamEvent[]
      }
    />
  )
  fireEvent.click(screen.getByText('参考了 1 条项目记忆'))
  expect(screen.getByRole('link')).toHaveAttribute('href', '/chat/s?dt_workspace=a')
  expect(screen.queryByText('Unsafe')).not.toBeInTheDocument()
})


it('preserves actionable workspace errors and translates them in the current UI language', async () => {
  const translated = (message: string) => projectErrorMessage(message, key => i18n.t(key))
  const known = 'A selected memory source is unavailable or is this project.'
  expect(translated(known)).toBe('所选记忆来源暂不可用，或选择了当前项目。请重新选择。')
  expect(translated('Workspace storage moved. Reload before continuing.')).toBe(
    '工作区存储位置已更改，请重新加载后继续。'
  )
  expect(translated('Request failed (401)')).toBe('登录已过期，请重新登录后继续。')
  expect(translated('Failed to fetch')).toBe('连接失败，请检查网络后重试。')
  await i18n.changeLanguage('en')
  expect(translated(known)).toBe(known)
  expect(translated('Request failed (401)')).toBe(
    'Your session has expired. Sign in again to continue.'
  )
})

it('gives an unknown server failure a retryable localized fallback', async () => {
  const translated = () => projectErrorMessage('Storage unavailable', key => i18n.t(key))
  expect(translated()).toBe('此项目操作暂未完成，请重新加载后重试。')
  await i18n.changeLanguage('en')
  expect(translated()).toBe('Could not complete this project action. Reload and try again.')
})
