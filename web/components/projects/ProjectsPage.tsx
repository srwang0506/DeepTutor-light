'use client'

import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
import Link from 'next/link'
import {
  ArrowLeft,
  ArrowRight,
  Archive,
  BookOpen,
  CheckCircle2,
  Circle,
  FolderOpen,
  Layers3,
  MessageCircle,
  Plus,
  Search,
  Settings2,
  Sparkles,
  RotateCcw,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { FeaturePage } from '@/components/layout/FeaturePage'
import { useActiveWorkspaceId } from '@/hooks/useActiveWorkspaceId'
import { scopedUrl, selectWorkspace } from '@/lib/workspace-scope'
import {
  getProjectOverview,
  listWorkspaces,
  recallProjectMemory,
  saveWorkspace,
  workspaceLabel,
  type ChatWorkspaceRegistration,
  type ProjectOverview,
  type ProjectRecallResult,
} from '@/lib/workspaces-api'
import ProjectEditor from './ProjectEditor'
import './projects.css'

function Mark({ row, large = false }: { row: ChatWorkspaceRegistration; large?: boolean }) {
  return (
    <span
      className={`project-mark ${large ? 'large' : ''}`}
      style={{ '--project-color': row.color ?? '#477965' } as CSSProperties}
      aria-hidden
    >
      <FolderOpen size={large ? 28 : 22} strokeWidth={1.4} />
    </span>
  )
}

export default function ProjectsPage({ detail = false }: { detail?: boolean }) {
  const { i18n } = useTranslation(),
    zh = i18n.language.startsWith('zh')
  const active = useActiveWorkspaceId()
  const [projects, setProjects] = useState<ChatWorkspaceRegistration[]>([])
  const [overview, setOverview] = useState<ProjectOverview | null>(null)
  const [loading, setLoading] = useState(true),
    [error, setError] = useState('')
  const [filter, setFilter] = useState(''),
    [archived, setArchived] = useState(false)
  const [editor, setEditor] = useState<ChatWorkspaceRegistration | 'new' | null>(null)
  const [query, setQuery] = useState(''),
    [recall, setRecall] = useState<ProjectRecallResult | null>(null)
  const [searching, setSearching] = useState(false),
    [mutation, setMutation] = useState(false)
  const [notice, setNotice] = useState('')
  const sequence = useRef(0),
    searchSequence = useRef(0)
  const load = useCallback(async () => {
    const version = ++sequence.current
    setLoading(true)
    setError('')
    try {
      const [rows, snapshot] = await Promise.all([
        listWorkspaces(true),
        detail ? getProjectOverview(active) : Promise.resolve(null),
      ])
      if (version !== sequence.current) return
      setProjects(rows.filter(r => r.kind !== 'system'))
      setOverview(snapshot)
    } catch (e) {
      if (version === sequence.current) setError(e instanceof Error ? e.message : String(e))
    } finally {
      if (version === sequence.current) setLoading(false)
    }
  }, [active, detail])
  useEffect(() => {
    void load()
    return () => {
      sequence.current++
      searchSequence.current++
    }
  }, [load])
  const selected = overview?.workspace
  const visible = projects
    .filter(
      p =>
        p.archived === archived &&
        `${p.display_name} ${p.description ?? ''}`.toLowerCase().includes(filter.toLowerCase())
    )
    .sort((a, b) =>
      a.kind === 'general'
        ? 1
        : b.kind === 'general'
          ? -1
          : b.created_at.localeCompare(a.created_at)
    )
  async function archive(row: ChatWorkspaceRegistration) {
    if (mutation) return
    setMutation(true)
    setError('')
    try {
      await saveWorkspace({ archived: !row.archived }, row.workspace_id)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setMutation(false)
    }
  }
  async function search() {
    if (!query.trim() || searching) return
    const version = ++searchSequence.current
    setSearching(true)
    setError('')
    try {
      const result = await recallProjectMemory(active, query.trim())
      if (version === searchSequence.current) setRecall(result)
    } catch (e) {
      if (version === searchSequence.current) setError(e instanceof Error ? e.message : String(e))
    } finally {
      if (version === searchSequence.current) setSearching(false)
    }
  }
  const open = (row: ChatWorkspaceRegistration) =>
    void selectWorkspace(row.kind === 'general' ? '' : row.workspace_id, '/project')
  return (
    <FeaturePage className="projects-page">
      <div className="projects-shell">
        {detail && (
          <Link href="/projects" className="project-back">
            <ArrowLeft size={15} />
            {zh ? '所有项目' : 'All projects'}
          </Link>
        )}
        <header className="projects-header">
          <div>
            <div className="projects-eyebrow">
              {zh ? '你的学习与科研空间' : 'YOUR SPACE TO LEARN & DISCOVER'}
            </div>
            <h1>
              {detail
                ? selected
                  ? workspaceLabel(selected, zh)
                  : zh
                    ? '项目'
                    : 'Project'
                : zh
                  ? '让每个想法，有自己的空间。'
                  : 'A place for every idea.'}
            </h1>
            <p>
              {detail
                ? selected?.description ||
                  (zh
                    ? '从上次停下的地方继续，把思考留在这个项目里。'
                    : 'Pick up where you left off. Keep your thinking together.')
                : zh
                  ? '从一门课程到一个研究方向，把对话、资料与进展收在一起。'
                  : 'From a course to a research question, keep conversations, materials and progress together.'}
            </p>
          </div>
          {detail ? (
            <button
              className="project-button"
              disabled={!selected}
              onClick={() => setEditor(selected!)}
            >
              <Settings2 size={16} />
              {zh ? '项目设置' : 'Settings'}
            </button>
          ) : (
            <button className="project-button primary" onClick={() => setEditor('new')}>
              <Plus size={17} />
              {zh ? '新建项目' : 'New project'}
            </button>
          )}
        </header>
        {notice && (
          <p className="project-notice" role="status">
            <CheckCircle2 size={16} />
            {notice}
          </p>
        )}
        {error && (
          <div className="project-error" role="alert">
            {error}
            <button className="project-text-button" onClick={() => void load()}>
              {zh ? '重新加载' : 'Reload'}
            </button>
          </div>
        )}
        {loading && (
          <div className="project-loading" role="status">
            <span />
            {zh ? '正在整理项目…' : 'Loading your projects…'}
          </div>
        )}
        {!detail && !loading && (
          <>
            <div className="project-toolbar">
              <div
                className="project-tabs"
                role="group"
                aria-label={zh ? '项目状态' : 'Project status'}
              >
                <button
                  aria-pressed={!archived}
                  className={!archived ? 'active' : ''}
                  onClick={() => setArchived(false)}
                >
                  {zh ? '我的项目' : 'My projects'}{' '}
                  <span>{projects.filter(p => !p.archived).length}</span>
                </button>
                <button
                  aria-pressed={archived}
                  className={archived ? 'active' : ''}
                  onClick={() => setArchived(true)}
                >
                  {zh ? '已归档' : 'Archived'}
                </button>
              </div>
              <label className="project-search">
                <Search size={16} />
                <input
                  aria-label={zh ? '搜索项目' : 'Search projects'}
                  value={filter}
                  onChange={e => setFilter(e.target.value)}
                  placeholder={zh ? '查找一个项目' : 'Find a project'}
                />
              </label>
            </div>
            <div className="project-grid">
              {visible.map(row => (
                <article
                  className={`project-card ${row.kind === 'general' ? 'general' : ''}`}
                  key={row.workspace_id}
                >
                  <div className="project-card-top">
                    <Mark row={row} />
                    <span>
                      {row.kind === 'general'
                        ? zh
                          ? '日常空间'
                          : 'Everyday'
                        : row.workspace_id === active
                          ? zh
                            ? '当前项目'
                            : 'Current'
                          : zh
                            ? '独立项目'
                            : 'Project'}
                    </span>
                  </div>
                  <button
                    className="project-card-title"
                    onClick={() => open(row)}
                    disabled={row.status !== 'ready'}
                  >
                    {workspaceLabel(row, zh)}
                    <ArrowUpRightIcon />
                  </button>
                  <p>
                    {row.description ||
                      (row.kind === 'general'
                        ? zh
                          ? '随手一问，偶然的灵感，以及尚未归入项目的想法。'
                          : 'Quick questions, passing inspiration and ideas yet to find a project.'
                        : zh
                          ? '在这里开始一段新的探索。'
                          : 'Start a new exploration here.')}
                  </p>
                  <div className="project-card-bottom">
                    <span>
                      <Layers3 size={14} />
                      {row.memory_policy?.sources.length
                        ? zh
                          ? `参考 ${row.memory_policy.sources.length} 个项目`
                          : `${row.memory_policy.sources.length} memory sources`
                        : zh
                          ? '仅本项目记忆'
                          : 'Project-only memory'}
                    </span>
                    {row.archived ? (
                      <button
                        aria-label={zh ? `恢复 ${row.display_name}` : `Restore ${row.display_name}`}
                        disabled={mutation}
                        onClick={() => void archive(row)}
                      >
                        <RotateCcw size={15} />
                      </button>
                    ) : (
                      <button
                        aria-label={
                          zh ? `${row.display_name} 的设置` : `Settings for ${row.display_name}`
                        }
                        onClick={() => setEditor(row)}
                      >
                        <Settings2 size={15} />
                      </button>
                    )}
                  </div>
                  {row.status !== 'ready' && (
                    <small className="project-error">
                      {zh ? '项目存储暂不可用' : 'Project storage unavailable'}
                    </small>
                  )}
                </article>
              ))}
              {!archived && !filter && (
                <button className="project-create-card" onClick={() => setEditor('new')}>
                  <span>
                    <Plus size={24} />
                  </span>
                  <strong>{zh ? '下一个想研究什么？' : 'What will you explore next?'}</strong>
                  <p>
                    {zh
                      ? '创建项目，选择想延续的记忆。'
                      : 'Create a project. Choose what to carry forward.'}
                  </p>
                </button>
              )}
            </div>
            {!visible.length && (archived || filter) && (
              <div className="project-empty">
                <FolderOpen size={28} />
                <p>{zh ? '这里还没有项目。' : 'No projects here yet.'}</p>
              </div>
            )}
            <aside className="project-explainer">
              <Layers3 size={22} strokeWidth={1.3} />
              <div>
                <strong>
                  {zh ? '独立思考，也能接续过去。' : 'Independent spaces. Connected thinking.'}
                </strong>
                <p>
                  {zh
                    ? '每个项目都有独立记忆。需要时，选择几个项目作为参考；来源始终清晰，新记忆只留在当前项目。'
                    : 'Each project keeps its own memory. Select a few others when useful; references keep their sources, and new memories stay here.'}
                </p>
              </div>
            </aside>
          </>
        )}
        {detail && selected && overview && !loading && (
          <>
            <div className="project-detail-hero">
              <Mark row={selected} large />
              <div>
                <strong>{zh ? '准备好继续了吗？' : 'Ready to pick things up?'}</strong>
                <p>
                  {selected.instructions
                    ? zh
                      ? '已设置专属项目指令'
                      : 'Custom project instructions active'
                    : zh
                      ? '把问题、思路或新的发现带进来。'
                      : 'Bring a question, an idea, or a new discovery.'}
                </p>
              </div>
              <Link className="project-button primary" href={scopedUrl('/chat', active)}>
                <Plus size={16} />
                {zh ? '开始新对话' : 'New conversation'}
              </Link>
            </div>
            <div className="project-detail-grid">
              <div className="project-main-column">
                <section className="project-panel">
                  <div className="project-section-title">
                    <h2>
                      <MessageCircle size={17} />
                      {zh ? '最近的思考' : 'Recent conversations'}
                    </h2>
                    <span>{overview.counts.sessions}</span>
                  </div>
                  {overview.sessions.length ? (
                    overview.sessions.map(s => (
                      <Link
                        className="project-activity"
                        key={s.id}
                        href={scopedUrl(`/chat/${encodeURIComponent(s.id)}`, active)}
                      >
                        <MessageCircle size={16} />
                        <div>
                          <strong>{s.title || (zh ? '未命名对话' : 'Untitled')}</strong>
                          <small>
                            {new Date(s.updated_at * 1000).toLocaleDateString(
                              zh ? 'zh-CN' : 'en-US',
                              { month: 'short', day: 'numeric' }
                            )}
                          </small>
                        </div>
                        <ArrowRight size={15} />
                      </Link>
                    ))
                  ) : (
                    <div className="project-empty">
                      <MessageCircle size={24} />
                      <p>
                        {zh
                          ? '第一段对话，是这个项目的起点。'
                          : 'The first conversation starts your project.'}
                      </p>
                      <Link href={scopedUrl('/chat', active)}>
                        {zh ? '聊聊你的想法' : 'Start with an idea'} →
                      </Link>
                    </div>
                  )}
                </section>
                <section className="project-panel">
                  <div className="project-section-title">
                    <h2>
                      <CheckCircle2 size={17} />
                      {zh ? '正在推进' : 'In progress'}
                    </h2>
                    <Link href={scopedUrl('/kanban', active)}>
                      {zh ? '打开任务板' : 'Task board'} <ArrowRight size={13} />
                    </Link>
                  </div>
                  {overview.tasks.length ? (
                    overview.tasks.map(t => (
                      <Link className="project-task" key={t.id} href={scopedUrl('/kanban', active)}>
                        {t.status === 'done' ? <CheckCircle2 size={16} /> : <Circle size={16} />}
                        <span>{t.title}</span>
                        <small>
                          {t.status === 'done'
                            ? zh
                              ? '完成'
                              : 'Done'
                            : t.status === 'doing'
                              ? zh
                                ? '进行中'
                                : 'Doing'
                              : zh
                                ? '待办'
                                : 'To do'}
                        </small>
                      </Link>
                    ))
                  ) : (
                    <div className="project-empty compact">
                      <p>{zh ? '把接下来要做的事记下来。' : 'Capture your next steps.'}</p>
                      <Link href={scopedUrl('/kanban', active)}>
                        {zh ? '添加第一个任务' : 'Add a first task'} →
                      </Link>
                    </div>
                  )}
                </section>
              </div>
              <aside className="project-side-column">
                <section className="project-panel memory">
                  <div className="project-section-title">
                    <h2>
                      <Layers3 size={17} />
                      {zh ? '记忆连接' : 'Memory connections'}
                    </h2>
                    <button
                      aria-label={zh ? '编辑记忆来源' : 'Edit memory sources'}
                      onClick={() => setEditor(selected)}
                    >
                      <Settings2 size={15} />
                    </button>
                  </div>
                  <div className="project-connection local">
                    <span className="project-dot" style={{ background: selected.color }} />
                    <div>
                      <strong>{zh ? '本项目' : 'This project'}</strong>
                      <small>{zh ? '新记忆保存在这里' : 'New memories stay here'}</small>
                    </div>
                    <span className="project-tag">{zh ? '读写' : 'Read & write'}</span>
                  </div>
                  {overview.sources
                    .filter(s => s.id !== active)
                    .map(source => (
                      <div className="project-connection" key={source.id}>
                        <Layers3 size={15} />
                        <div>
                          <strong>
                            {source.id ? source.name : zh ? '通用工作区' : 'General workspace'}
                          </strong>
                          <small>
                            {zh ? '按需参考 · 保留来源' : 'On-demand · with attribution'}
                          </small>
                        </div>
                        <span className="project-tag">{zh ? '只读' : 'Read only'}</span>
                      </div>
                    ))}
                  {!selected.memory_policy?.sources.length && (
                    <p className="project-help">
                      {zh
                        ? '当前保持独立。你可以连接已有项目，让有用的上下文延续过来。'
                        : 'Independent for now. Connect another project to recall useful context.'}
                    </p>
                  )}
                  {!!overview.unavailable_sources.length && (
                    <p className="project-help">
                      {zh
                        ? `${overview.unavailable_sources.length} 个来源已暂停，当前不会被检索。`
                        : `${overview.unavailable_sources.length} sources are unavailable and excluded.`}
                    </p>
                  )}
                  <button className="project-text-button" onClick={() => setEditor(selected)}>
                    <Plus size={14} />
                    {zh ? '选择记忆来源' : 'Choose memory sources'}
                  </button>
                  <Link className="project-memory-manage" href={scopedUrl('/memory', active)}>
                    {zh ? '查看和编辑本项目记忆' : 'View & edit this project’s memory'}{' '}
                    <ArrowRight size={14} />
                  </Link>
                </section>
                <section className="project-panel">
                  <div className="project-section-title">
                    <h2>
                      <BookOpen size={17} />
                      {zh ? '项目资料' : 'Materials & research'}
                    </h2>
                  </div>
                  <Link className="project-resource" href={scopedUrl('/learning/reading', active)}>
                    <BookOpen size={18} />
                    <div>
                      <strong>{zh ? '阅读与资料' : 'Reading & materials'}</strong>
                      <small>
                        {zh ? '论文、教材和阅读批注' : 'Papers, textbooks and annotations'}
                      </small>
                    </div>
                    <ArrowRight size={15} />
                  </Link>
                  <Link className="project-resource" href={scopedUrl('/research', active)}>
                    <Sparkles size={18} />
                    <div>
                      <strong>{zh ? '科研工作台' : 'Research workbench'}</strong>
                      <small>
                        {zh
                          ? '文献对比、引用核查与实验方案'
                          : 'Comparisons, citations and experiments'}
                      </small>
                    </div>
                    <ArrowRight size={15} />
                  </Link>
                </section>
              </aside>
            </div>
            <section className="project-panel project-recall">
              <div className="project-section-title">
                <h2>
                  <Search size={17} />
                  {zh ? '看看这个项目能想起什么' : 'Explore what this project can recall'}
                </h2>
              </div>
              <p className="project-help">
                {zh
                  ? '搜索本项目及已选来源中的记忆、会话与任务。结果可直接回到原处。'
                  : 'Search memories, conversations and tasks from this project and selected sources.'}
              </p>
              <form
                onSubmit={e => {
                  e.preventDefault()
                  void search()
                }}
                className="project-recall-form"
              >
                <input
                  aria-label={zh ? '搜索项目记忆' : 'Search project memory'}
                  placeholder={
                    zh
                      ? '例如：HOI 的数据集选择，或上次讨论的动态规划…'
                      : 'Try: HOI datasets, or dynamic programming…'
                  }
                  maxLength={2000}
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                />
                <button className="project-button primary" disabled={searching || !query.trim()}>
                  {searching ? (zh ? '检索中…' : 'Searching…') : zh ? '查找记忆' : 'Recall'}
                </button>
              </form>
              {recall && (
                <div className="project-recall-results" aria-live="polite">
                  {!recall.hits.length && (
                    <p className="project-help">
                      {zh
                        ? '没有相关记忆。试试更明确的主题或关键词。'
                        : 'No relevant memories. Try a more specific topic.'}
                    </p>
                  )}
                  {recall.hits.map(hit => (
                    <Link
                      key={`${hit.workspace_id}:${hit.key}`}
                      href={hit.url}
                      className="project-recall-result"
                    >
                      <div>
                        <span className="project-tag">
                          {hit.external
                            ? hit.workspace_id
                              ? hit.workspace_name
                              : zh
                                ? '通用工作区'
                                : 'General workspace'
                            : zh
                              ? '本项目'
                              : 'This project'}
                        </span>
                        <small>
                          {hit.kind === 'chat'
                            ? zh
                              ? '会话'
                              : 'Conversation'
                            : hit.kind === 'task'
                              ? zh
                                ? '任务'
                                : 'Task'
                              : zh
                                ? '记忆'
                                : 'Memory'}
                        </small>
                        <ArrowRight size={14} />
                      </div>
                      <strong>{hit.title}</strong>
                      <p>{hit.text}</p>
                    </Link>
                  ))}
                  {recall.truncated && (
                    <p className="project-help">
                      {zh
                        ? '历史较多，目前检索近期内容与项目记忆。'
                        : 'This project has extensive history; recall currently covers recent history and saved memory.'}
                    </p>
                  )}
                </div>
              )}
            </section>
            {selected.kind !== 'general' && (
              <div className="project-footer">
                <span>
                  {zh
                    ? '归档保留内容，并暂停其他项目对它的记忆检索。'
                    : 'Archiving preserves content and pauses recall from other projects.'}
                </span>
                <button
                  className="project-text-button"
                  disabled={mutation}
                  onClick={() => void archive(selected)}
                >
                  {selected.archived ? <RotateCcw size={14} /> : <Archive size={14} />}
                  {selected.archived
                    ? zh
                      ? '恢复项目'
                      : 'Restore project'
                    : zh
                      ? '归档项目'
                      : 'Archive project'}
                </button>
              </div>
            )}
          </>
        )}
        {editor && (
          <ProjectEditor
            key={editor === 'new' ? 'new' : editor.workspace_id}
            project={editor === 'new' ? undefined : editor}
            projects={projects}
            zh={zh}
            onClose={() => setEditor(null)}
            onSaved={row => {
              const created = editor === 'new'
              setEditor(null)
              setRecall(null)
              setNotice(zh ? '项目与记忆来源已保存。' : 'Project and memory sources saved.')
              if (created) open(row)
              else void load()
            }}
          />
        )}
      </div>
    </FeaturePage>
  )
}
function ArrowUpRightIcon() {
  return <ArrowRight size={17} className="project-card-arrow" />
}
