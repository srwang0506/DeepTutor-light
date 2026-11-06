'use client'

import { useId, useState, startTransition, type ReactNode } from 'react'
import dynamic from 'next/dynamic'
import { usePathname, useRouter } from 'next/navigation'
import { m, useReducedMotion } from 'framer-motion'
import {
  MessageCircle,
  Search,
  FlaskConical,
  Puzzle,
  FilePenLine,
  Network,
  FolderOpen,
  Server,
  Layers3,
  ListTodo,
} from 'lucide-react'
import { HomeShelfIcon, LearningShelfIcon, ResourcesShelfIcon } from './ShelfIcons'
import { useTranslation } from 'react-i18next'
import Tooltip from '@/shared/ui/Tooltip'
import { categoryForPath, type NavigationCategory } from '@/lib/navigation'
import { navVisibleUnderPolicy } from './nav-entries'
import { useAuthStatus } from '@/hooks/useAuthStatus'
import { navigateTask } from '@/lib/workspace-scope'
import { OverviewRow } from './NavigationPrimitives'
import { SHORTCUTS } from '@/lib/shortcuts'
import type { LearningSessions } from './LearningNavigation'

// Feature libraries load when their category opens, not in every workspace.
const LearningNavigation = dynamic(() =>
  import('./LearningNavigation').then(module => module.LearningNavigation)
)

/**
 * The shelves, workspace content first. Partners and personalization belong
 * to the account — the same in every workspace — and sit past a divider so
 * the bar itself says which half a switch of workspace changes.
 */
const LEGACY_CATEGORIES = [
  { id: 'home', label: 'Study today', icon: HomeShelfIcon, href: '/today', account: false },
  {
    id: 'resources',
    label: 'Materials',
    icon: ResourcesShelfIcon,
    href: '/learning/reading',
    account: false,
  },
  {
    id: 'learning',
    label: 'Practice & records',
    icon: LearningShelfIcon,
    href: '/learning/practice',
    account: false,
  },
  {
    id: 'research',
    label: 'Research & ecosystem',
    icon: FlaskConical,
    href: '/research',
    account: false,
  },
] as const

export const CATEGORIES = [
  { id: 'home', label: 'Study today', icon: HomeShelfIcon, href: '/today', account: false },
  { id: 'resources', label: 'Library', icon: ResourcesShelfIcon, href: '/library', account: false },
  { id: 'research', label: 'Projects', icon: FolderOpen, href: '/projects', account: false },
] as const
type CategoryItem = (typeof CATEGORIES)[number] | (typeof LEGACY_CATEGORIES)[number]

export function useCategoryNavigation() {
  const pathname = usePathname()
  const { learningPolicy } = useAuthStatus()
  const categories: CategoryItem[] = (learningPolicy ? LEGACY_CATEGORIES : CATEGORIES).filter(
    item => navVisibleUnderPolicy(item.href, learningPolicy?.allowedSurfaces ?? null)
  )
  const [visited, setVisited] = useState<NavigationCategory[]>([])
  const [selection, setSelection] = useState<{
    pathname: string
    category: NavigationCategory
  } | null>(null)
  const desired = selection?.pathname === pathname ? selection.category : categoryForPath(pathname)
  const grouped =
    !learningPolicy && ['learning', 'partners', 'personalization'].includes(desired)
      ? 'research'
      : desired
  const category = categories.find(item => item.id === grouped)?.id ?? categories[0]?.id ?? 'home'
  return {
    category,
    categories,
    visited,
    select: (next: NavigationCategory) => {
      startTransition(() => {
        setVisited(previous => [...new Set([...previous, category, next])])
        setSelection({ pathname, category: next })
      })
    },
    allowedSurfaces: learningPolicy?.allowedSurfaces ?? null,
  }
}
type Navigation = ReturnType<typeof useCategoryNavigation>

export function CategoryTabs({
  nav,
  collapsed,
  onSearch,
}: {
  nav: Navigation
  collapsed: boolean
  /** The rail keeps search with its tabs; the open sidebar has it in its header. */
  onSearch?: () => void
}) {
  const { t } = useTranslation()
  const router = useRouter()
  const id = useId()
  const reduced = useReducedMotion()
  const [peek, setPeek] = useState(false)
  const select = (category: CategoryItem) => {
    nav.select(category.id)
    navigateTask(category.href, router.push)
  }
  const workspace = nav.categories.filter(item => !item.account)
  const account = nav.categories.filter(item => item.account)
  // The account's shelves fold into a small pair while the workspace is in
  // use, and open under the pointer, under focus, or once one is selected.
  const tucked = !collapsed && !peek && !account.some(item => item.id === nav.category)
  const tab = (item: CategoryItem) => {
    const index = nav.categories.indexOf(item)
    const active = nav.category === item.id
    return (
      <Tooltip
        key={item.id}
        label={t(item.label)}
        description={item.account ? t('navigation.accountShelf') : undefined}
        side={collapsed ? 'right' : 'bottom'}
        shortcut={SHORTCUTS.category(index)}
        // The active tab already prints its name — unless an account shelf's
        // longer label is truncated, so those keep theirs.
        suppressed={!collapsed && active && !item.account}
      >
        <button
          id={`category-${item.id}`}
          type="button"
          role="tab"
          aria-controls="category-content"
          aria-label={t(item.label)}
          aria-selected={active}
          tabIndex={active ? 0 : -1}
          onKeyDown={event => {
            const delta = ['ArrowRight', 'ArrowDown'].includes(event.key)
              ? 1
              : ['ArrowLeft', 'ArrowUp'].includes(event.key)
                ? -1
                : 0
            if (!delta && !['Home', 'End'].includes(event.key)) return
            event.preventDefault()
            const next =
              event.key === 'Home'
                ? 0
                : event.key === 'End'
                  ? nav.categories.length - 1
                  : (index + delta + nav.categories.length) % nav.categories.length
            select(nav.categories[next])
            event.currentTarget
              .closest('[role="tablist"]')
              ?.querySelectorAll<HTMLButtonElement>('[role="tab"]')
              [next]?.focus()
          }}
          onClick={() => select(item)}
          className={active ? 'is-active' : ''}
        >
          {active && (
            <m.span
              layoutId={`category-pill-${id}`}
              className="dt-category-pill"
              transition={{ duration: reduced ? 0 : 0.22, ease: [0.22, 1, 0.36, 1] }}
            />
          )}
          <item.icon size={20} strokeWidth={1.6} />
          <span>{t(item.label)}</span>
        </button>
      </Tooltip>
    )
  }
  return (
    <div className={`dt-category-bar ${collapsed ? 'is-collapsed' : ''}`}>
      <div role="tablist" aria-label={t('navigation.categories')} aria-orientation="vertical">
        {workspace.map(tab)}
        {account.length > 0 && (
          <>
            {workspace.length > 0 && <span aria-hidden className="dt-category-divider" />}
            <div
              className={`dt-account-tabs ${tucked ? 'is-tucked' : ''}`}
              onPointerEnter={event => {
                if (event.pointerType !== 'touch') setPeek(true)
              }}
              onPointerLeave={() => setPeek(false)}
              onFocus={() => setPeek(true)}
              onBlur={event => {
                if (!event.currentTarget.contains(event.relatedTarget as Node | null))
                  setPeek(false)
              }}
            >
              {account.map(tab)}
            </div>
          </>
        )}
      </div>
      {onSearch && (
        <Tooltip
          label={t('navigation.search')}
          side={collapsed ? 'right' : 'bottom'}
          shortcut={SHORTCUTS.search}
        >
          <button
            type="button"
            className="dt-nav-icon"
            aria-label={t('navigation.search')}
            aria-haspopup="dialog"
            onClick={onSearch}
          >
            <Search size={16} strokeWidth={1.6} />
          </button>
        </Tooltip>
      )}
    </div>
  )
}

export function CategoryBody({
  nav,
  collapsed,
  sessions,
  learningSessions,
}: {
  nav: Navigation
  collapsed: boolean
  /** The home shelf's conversation records. */
  sessions: ReactNode
  /** Learning conversations, filed by the learning shelf under their records. */
  learningSessions?: LearningSessions
}) {
  const { t } = useTranslation()
  if (collapsed) return null
  return (
    <div
      id="category-content"
      role="tabpanel"
      aria-labelledby={`category-${nav.category}`}
      className={`dt-category-content ${['partners', 'learning'].includes(nav.category) ? 'dt-category-content-fixed' : ''}`}
    >
      {nav.category === 'home' && (
        <>
          {/* The tutor itself leads the shelf; the tools it works with follow. */}
          {navVisibleUnderPolicy('/chat', nav.allowedSurfaces) && (
            <OverviewRow href="/chat" label={t('navigation.chat')} icon={MessageCircle} />
          )}

          {sessions}
        </>
      )}
      {nav.category === 'research' && (
        <div className="space-y-1">
          <OverviewRow href="/project" label={t('Projects')} icon={FolderOpen} />
          <OverviewRow href="/learning/practice" label={t('Practice & records')} icon={ListTodo} />
          <OverviewRow href="/studio/evidence" label="证据与比较" icon={Layers3} />
          <OverviewRow href="/studio/experiments" label="实验记录" icon={FlaskConical} />
          <OverviewRow href="/studio/writing" label="论文写作" icon={FilePenLine} />
          <OverviewRow href="/studio/connections" label="远程工作区" icon={Server} />
          <OverviewRow href="/research" label={t('Research workbench')} icon={FlaskConical} />
          <OverviewRow
            href="/chat?capability=deep_research"
            label={t('Deep research')}
            icon={Search}
          />
          <OverviewRow href="/co-writer" label={t('Co-Writer')} icon={FilePenLine} />
          <OverviewRow href="/partners" label={t('Partners')} icon={Network} />
          <OverviewRow href="/space" label={t('Explore the ecosystem')} icon={Puzzle} />
        </div>
      )}
      {(nav.category === 'learning' || nav.visited.includes('learning')) && (
        <div hidden={nav.category !== 'learning'} className="dt-category-panel">
          <LearningNavigation active={nav.category === 'learning'} sessions={learningSessions} />
        </div>
      )}
      {(nav.category === 'resources' || nav.visited.includes('resources')) && (
        <div hidden={nav.category !== 'resources'}>
          {!nav.allowedSurfaces && (
            <OverviewRow href="/library" label={t('Library')} icon={ResourcesShelfIcon} />
          )}
          <OverviewRow href="/learning/reading" label={t('Materials')} icon={ResourcesShelfIcon} />
          <OverviewRow href="/knowledge-bases" label={t('Knowledge Bases')} icon={Search} />
        </div>
      )}
    </div>
  )
}
