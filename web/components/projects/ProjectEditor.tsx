'use client'

import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, Layers3, LockKeyhole, ArrowUpRight } from 'lucide-react'
import { Dialog } from '@/shared/ui/Dialog'
import { saveWorkspace, workspaceLabel, type ChatWorkspaceRegistration } from '@/lib/workspaces-api'
import { projectErrorMessage } from './project-feedback'

const colors = ['#477965', '#547b9c', '#8976a8', '#b87b56', '#b5667e', '#78844c']
export default function ProjectEditor({
  project,
  projects,
  zh,
  onClose,
  onSaved,
}: {
  project?: ChatWorkspaceRegistration
  projects: ChatWorkspaceRegistration[]
  zh: boolean
  onClose: () => void
  onSaved: (row: ChatWorkspaceRegistration) => void
}) {
  const { t } = useTranslation()
  const [name, setName] = useState(project?.display_name ?? '')
  const [description, setDescription] = useState(project?.description ?? '')
  const [instructions, setInstructions] = useState(project?.instructions ?? '')
  const [color, setColor] = useState(project?.color ?? colors[0])
  const [sources, setSources] = useState(project?.memory_policy?.sources ?? [])
  const [discoverable, setDiscoverable] = useState(project?.memory_policy?.discoverable ?? true)
  const [filter, setFilter] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const choices = projects.filter(
    p => p.kind !== 'system' && p.workspace_id !== project?.workspace_id
  )
  const sourceId = (row: ChatWorkspaceRegistration) =>
    row.kind === 'general' ? '' : row.workspace_id
  const available = (row: ChatWorkspaceRegistration) =>
    !row.archived && row.status === 'ready' && row.memory_policy?.discoverable !== false
  const valid = name.trim().length > 0 && sources.length <= 24
  async function save() {
    if (!valid || busy) return
    setBusy(true)
    setError('')
    try {
      const row = await saveWorkspace(
        {
          ...(project?.kind === 'general' ? {} : { name: name.trim() }),
          description,
          instructions,
          color,
          memory_policy: { sources, discoverable },
        },
        project?.workspace_id
      )
      onSaved(row)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setBusy(false)
    }
  }
  return (
    <Dialog
      open
      title={
        project
          ? zh
            ? '项目设置'
            : 'Project settings'
          : zh
            ? '为一件事，开一个项目'
            : 'A space for what matters'
      }
      description={
        zh
          ? '会话、资料、任务和新记忆都留在这里。'
          : 'Conversations, materials, tasks and new memories stay here.'
      }
      onClose={onClose}
      busy={busy}
      size="lg"
      closeLabel={zh ? '关闭' : 'Close'}
      footer={
        <>
          <button className="project-button" onClick={onClose} disabled={busy}>
            {zh ? '取消' : 'Cancel'}
          </button>
          <button
            className="project-button primary"
            onClick={() => void save()}
            disabled={!valid || busy}
          >
            {busy
              ? zh
                ? '正在保存…'
                : 'Saving…'
              : project
                ? zh
                  ? '保存设置'
                  : 'Save changes'
                : zh
                  ? '创建项目'
                  : 'Create project'}
          </button>
        </>
      }
    >
      <div className="project-editor">
        <label>
          {zh ? '项目名称' : 'Project name'}
          <input
            data-autofocus
            value={project?.kind === 'general' ? workspaceLabel(project, zh) : name}
            onChange={e => setName(e.target.value)}
            maxLength={100}
            disabled={project?.kind === 'general'}
            placeholder={zh ? '例如：数据结构与算法' : 'e.g. Human–object interaction'}
          />
        </label>
        <div className="project-colors" role="group" aria-label={zh ? '项目颜色' : 'Project color'}>
          {colors.map((c, i) => (
            <button
              key={c}
              type="button"
              style={{ backgroundColor: c }}
              aria-label={`${zh ? '颜色' : 'Color'} ${i + 1}`}
              aria-pressed={color === c}
              onClick={() => setColor(c)}
            >
              {color === c && <Check size={15} />}
            </button>
          ))}
        </div>
        <label>
          {zh ? '一句话描述' : 'Description'}
          <input
            value={description}
            onChange={e => setDescription(e.target.value)}
            maxLength={600}
            placeholder={
              zh
                ? '这个项目里，你想探索或完成什么？'
                : 'What would you like to explore or accomplish?'
            }
          />
        </label>
        <section className="project-memory-picker" aria-labelledby="project-memory-heading">
          <div className="project-picker-title">
            <Layers3 size={19} />
            <div>
              <h3 id="project-memory-heading">
                {zh ? '这个项目可以参考哪些记忆？' : 'Which memories can this project use?'}
              </h3>
              <p>
                {zh
                  ? '本项目始终可用。其他项目由你选择，随时可以更改。'
                  : 'Its own memory is always available. Choose any other sources below.'}
              </p>
            </div>
          </div>
          <div className="project-local-memory">
            <LockKeyhole size={15} />
            <span>{zh ? '本项目记忆' : 'This project’s memory'}</span>
            <span>{zh ? '独立保存 · 始终可用' : 'Separate · always available'}</span>
            <Check size={15} />
          </div>
          {choices.length > 4 && (
            <input
              aria-label={zh ? '搜索记忆来源' : 'Find memory sources'}
              placeholder={zh ? '查找项目…' : 'Find a project…'}
              value={filter}
              onChange={e => setFilter(e.target.value)}
            />
          )}
          <div className="project-source-options">
            {choices
              .filter(p => workspaceLabel(p, zh).toLowerCase().includes(filter.toLowerCase()))
              .map(row => {
                const id = sourceId(row),
                  chosen = sources.includes(id),
                  enabled = available(row)
                return (
                  <label
                    className={`project-source-option ${chosen ? 'selected' : ''}`}
                    key={row.workspace_id}
                  >
                    <input
                      type="checkbox"
                      checked={chosen}
                      disabled={!enabled && !chosen}
                      onChange={() =>
                        setSources(prev => (chosen ? prev.filter(s => s !== id) : [...prev, id]))
                      }
                    />
                    <span className="project-dot" style={{ background: row.color ?? colors[0] }} />
                    <span>
                      <strong>{workspaceLabel(row, zh)}</strong>
                      <small>
                        {!enabled
                          ? zh
                            ? '来源暂停，可移除选择'
                            : 'Unavailable · remove to update'
                          : row.description ||
                            (row.kind === 'general'
                              ? zh
                                ? '通用聊天与日常交互'
                                : 'Everyday conversations'
                              : zh
                                ? '记忆、历史会话与任务'
                                : 'Memory, conversations and tasks')}
                      </small>
                    </span>
                    <ArrowUpRight size={14} />
                  </label>
                )
              })}
          </div>
          {sources.length > 0 && (
            <button type="button" className="project-text-button" onClick={() => setSources([])}>
              {zh ? '取消所有外部来源' : 'Clear external sources'}
            </button>
          )}
          <p className="project-picker-foot">
            {sources.length
              ? zh
                ? `已选择 ${sources.length} 个外部来源 · 仅按需引用，不会复制或回写`
                : `${sources.length} external sources · recalled as needed, never copied or written back`
              : zh
                ? '仅使用本项目记忆，保持完全独立。'
                : 'Only this project’s memory. Fully independent.'}
          </p>
        </section>
        <details className="project-advanced">
          <summary>{zh ? '项目指令与引用权限' : 'Instructions & sharing preferences'}</summary>
          <label>
            {zh ? '项目指令' : 'Project instructions'}
            <textarea
              value={instructions}
              onChange={e => setInstructions(e.target.value)}
              rows={4}
              maxLength={10000}
              placeholder={
                zh
                  ? '例如：先讲直觉，再给出算法、复杂度和一道练习。'
                  : 'How should the assistant work in this project?'
              }
            />
          </label>
          <label className="project-discoverable">
            <input
              type="checkbox"
              checked={discoverable}
              onChange={e => setDiscoverable(e.target.checked)}
            />
            <span>
              {zh
                ? '允许我的其他项目将此项目选为记忆来源'
                : 'Allow my other projects to select this as a memory source'}
            </span>
          </label>
        </details>
        {sources.length > 24 && (
          <p role="alert">{zh ? '最多选择 24 个来源。' : 'Choose up to 24 sources.'}</p>
        )}
        {error && (
          <p className="project-error" role="alert">
            {projectErrorMessage(error, t)}
          </p>
        )}
      </div>
    </Dialog>
  )
}
