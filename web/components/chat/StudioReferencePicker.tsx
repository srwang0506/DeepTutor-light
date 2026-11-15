'use client'

import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { BookOpen, FlaskConical, Layers3, Search, Server } from 'lucide-react'
import { Button } from '@/shared/ui/Button'
import { Dialog } from '@/shared/ui/Dialog'
import { InlineAlert } from '@/shared/ui/InlineAlert'
import { studio } from '@/lib/studio-api'
import { useRecoveryPrincipal } from '@/hooks/useRecoveryPrincipal'
import { scopedUrl } from '@/lib/workspace-scope'
import {
  STUDIO_SOURCE_LIMIT,
  studioReferenceKey,
  studioReferenceHref,
  type StudioSourceItem,
} from '@/lib/studio-references'

export function studioSourceKind(kind: StudioSourceItem['ref']['kind']) {
  switch (kind) {
    case 'edition':
      return { label: '资料库', Icon: BookOpen }
    case 'run':
      return { label: '实验记录', Icon: FlaskConical }
    case 'remote_snapshot':
      return { label: '读取快照', Icon: Server }
    default:
      return { label: '原文证据', Icon: Layers3 }
  }
}

interface SourcePage {
  items: StudioSourceItem[]
  next_cursor?: string
}

function sourceRowKey(item: StudioSourceItem) {
  return studioReferenceKey({ ...item.ref, locator: { blob_hash: item.ref.locator?.blob_hash } })
}

export default function StudioReferencePicker(props: PickerProps) {
  const principal = useRecoveryPrincipal()
  return principal ? <Picker key={JSON.stringify([principal, props.workspace])} {...props} /> : null
}

type PickerProps = {
  workspace: string
  initial: StudioSourceItem[]
  onApply: (items: StudioSourceItem[]) => void
  onClose: () => void
  limit?: number
}

function Picker({
  workspace,
  initial,
  onApply,
  onClose,
  limit = STUDIO_SOURCE_LIMIT,
}: PickerProps) {
  const { t } = useTranslation()
  const [query, setQuery] = useState('')
  const [network, setNetwork] = useState<{ identity: string; data?: SourcePage; error?: boolean }>({
    identity: '',
  })
  const [selected, setSelected] = useState(initial)
  const [loadingFor, setLoadingFor] = useState('')
  const paging = useRef<AbortController | null>(null)
  const [retry, setRetry] = useState(0)
  const [pages, setPages] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      initial
        .filter(item => item.ref.locator?.page)
        .map(item => [sourceRowKey(item), String(item.ref.locator!.page)])
    )
  )
  const [invalidPages, setInvalidPages] = useState<Record<string, boolean>>({})
  const identity = JSON.stringify([workspace, query, retry])
  const loadingMore = loadingFor === identity
  const active = network.identity === identity ? network : null
  const page = active?.data
  useEffect(() => {
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams({ q: query.trim(), limit: '30' })
      void studio<SourcePage>(workspace, `/tutor-sources?${params}`, {}, controller.signal)
        .then(data => {
          if (!controller.signal.aborted) setNetwork({ identity, data })
        })
        .catch(() => {
          if (!controller.signal.aborted) setNetwork({ identity, error: true })
        })
    }, 120)
    return () => {
      window.clearTimeout(timer)
      controller.abort()
      paging.current?.abort()
      paging.current = null
    }
  }, [workspace, query, identity])
  async function loadMore() {
    if (!page?.next_cursor || paging.current) return
    const controller = new AbortController()
    paging.current = controller
    setLoadingFor(identity)
    try {
      const params = new URLSearchParams({ q: query.trim(), limit: '30', cursor: page.next_cursor })
      const next = await studio<SourcePage>(
        workspace,
        `/tutor-sources?${params}`,
        {},
        controller.signal
      )
      if (controller.signal.aborted) return
      setNetwork(current =>
        current.identity !== identity
          ? current
          : {
              identity,
              data: {
                ...next,
                items: Array.from(
                  new Map(
                    [...(current.data?.items ?? []), ...next.items].map(item => [
                      studioReferenceKey(item.ref),
                      item,
                    ])
                  ).values()
                ),
              },
            }
      )
    } catch {
      if (!controller.signal.aborted)
        setNetwork(current =>
          current.identity === identity ? { ...current, error: true } : current
        )
    } finally {
      if (paging.current === controller) {
        paging.current = null
        setLoadingFor('')
      }
    }
  }
  function itemAtPage(item: StudioSourceItem) {
    const value = Number(pages[sourceRowKey(item)])
    if (!item.pages || !Number.isSafeInteger(value) || value < 1 || value > item.pages) return item
    const ref = { ...item.ref, locator: { ...item.ref.locator, page: value } }
    return {
      ...item,
      ref,
      href: scopedUrl(studioReferenceHref(ref), ref.scope === '@library' ? workspace : ref.scope),
    }
  }
  function changePage(base: StudioSourceItem, value: string) {
    const key = sourceRowKey(base)
    setPages(previous => ({ ...previous, [key]: value }))
    const page = Number(value)
    const invalid =
      !!value.trim() && (!Number.isSafeInteger(page) || page < 1 || page > (base.pages ?? 0))
    setInvalidPages(previous => ({ ...previous, [key]: invalid }))
    if (invalid) return
    const ref = {
      ...base.ref,
      locator: { blob_hash: base.ref.locator?.blob_hash, ...(value.trim() ? { page } : {}) },
    }
    const replacement = {
      ...base,
      ref,
      href: scopedUrl(studioReferenceHref(ref), ref.scope === '@library' ? workspace : ref.scope),
    }
    setSelected(previous =>
      previous.some(item => sourceRowKey(item) === key)
        ? [...previous.filter(item => sourceRowKey(item) !== key), replacement]
        : previous
    )
  }
  const invalidSelection = selected.some(item => invalidPages[sourceRowKey(item)])
  return (
    <Dialog
      open
      title={t('Research sources')}
      description={t(
        'Add papers, evidence, experiment records, or saved remote snapshots to the next message.'
      )}
      onClose={onClose}
      size="md"
      footer={
        <div className="flex w-full flex-wrap items-center justify-between gap-3">
          <span role="status" className="text-sm text-muted-foreground">
            {t('{{count}} of {{limit}} sources selected', { count: selected.length, limit })}
          </span>
          <div className="flex gap-2">
            <Button variant="secondary" onClick={onClose}>
              {t('Cancel')}
            </Button>
            <Button disabled={invalidSelection} onClick={() => onApply(selected)}>
              {t('Use sources')}
            </Button>
          </div>
        </div>
      }
    >
      <label className="flex items-center gap-2 rounded-xl border border-border px-3 focus-within:ring-2 focus-within:ring-ring">
        <Search aria-hidden size={16} />
        <input
          className="min-h-10 min-w-0 flex-1 bg-transparent text-sm outline-none"
          placeholder={t('Search research sources')}
          aria-label={t('Search research sources')}
          value={query}
          onChange={event => setQuery(event.target.value)}
        />
      </label>
      {selected.length > 0 && (
        <ul className="mt-3 flex flex-wrap gap-1.5" aria-label={t('Selected sources')}>
          {selected.map(item => (
            <li
              key={studioReferenceKey(item.ref)}
              className="flex min-w-0 items-center gap-1 rounded-lg bg-muted py-1 pl-2 pr-1 text-xs"
            >
              <span className="max-w-48 truncate">
                {item.title}
                {item.ref.locator?.page
                  ? ` · ${t('Page {{number}}', { number: item.ref.locator.page })}`
                  : ''}
              </span>
              <button
                type="button"
                className="size-6 rounded hover:bg-background"
                aria-label={t('Remove source: {{title}}', { title: item.title })}
                onClick={() =>
                  setSelected(previous =>
                    previous.filter(
                      row => studioReferenceKey(row.ref) !== studioReferenceKey(item.ref)
                    )
                  )
                }
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      {active?.error && (
        <InlineAlert tone="danger" className="my-3">
          {t('Unable to load research sources.')}{' '}
          <button type="button" className="underline" onClick={() => setRetry(n => n + 1)}>
            {t('Retry')}
          </button>
        </InlineAlert>
      )}
      {!page && !active?.error && (
        <p role="status" className="my-4 text-sm text-muted-foreground">
          {t('Loading...')}
        </p>
      )}
      {page?.items.length === 0 && (
        <p className="my-4 text-sm text-muted-foreground">{t('No research sources found.')}</p>
      )}
      {!!page?.items.length && (
        <ul className="mt-3 max-h-[45dvh] space-y-2 overflow-y-auto">
          {page.items.map(base => {
            const row = itemAtPage(base)
            const baseKey = sourceRowKey(base)
            const checked = selected.some(item => sourceRowKey(item) === baseKey)
            const pageInput = pages[baseKey]?.trim()
            const invalidPage =
              !!pageInput &&
              (!Number.isSafeInteger(Number(pageInput)) ||
                Number(pageInput) < 1 ||
                Number(pageInput) > (base.pages ?? 0))
            const { label, Icon } = studioSourceKind(row.ref.kind)
            return (
              <li key={baseKey} className="rounded-xl border border-border p-3">
                <label className="flex min-w-0 items-start gap-2.5">
                  <input
                    type="checkbox"
                    className="mt-1 accent-primary"
                    checked={checked}
                    disabled={!checked && (selected.length >= limit || invalidPage)}
                    onChange={event => {
                      const include = event.target.checked
                      setSelected(previous =>
                        include
                          ? [...previous, row].slice(0, limit)
                          : previous.filter(item => sourceRowKey(item) !== baseKey)
                      )
                    }}
                  />
                  <Icon size={16} aria-hidden className="mt-0.5 shrink-0 text-muted-foreground" />
                  <span className="min-w-0">
                    <strong
                      title={row.title}
                      className="line-clamp-2 break-words text-sm font-medium"
                    >
                      {row.title}
                    </strong>
                    <span className="mt-1 block text-xs text-muted-foreground">
                      {t(label)} · {t('Revision {{number}}', { number: row.ref.revision })}
                      {row.workspace_name && ` · ${row.workspace_name}`}
                      {row.external && ` · ${t('Shared with this project')}`}
                    </span>
                    {row.description && (
                      <span className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                        {row.description}
                      </span>
                    )}
                  </span>
                </label>
                {!!base.pages && !!base.ref.locator?.blob_hash && (
                  <label className="ml-7 mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    {t('Physical page (optional)')}
                    <input
                      type="number"
                      min={1}
                      max={base.pages}
                      step={1}
                      className="w-20 rounded-md border border-border bg-background px-2 py-1 text-foreground"
                      value={pages[baseKey] ?? ''}
                      aria-invalid={invalidPage || undefined}
                      onChange={event => changePage(base, event.target.value)}
                    />
                  </label>
                )}
                {invalidPage && (
                  <p role="alert" className="ml-7 mt-1 text-xs text-destructive">
                    {t('Enter a physical page from 1 to {{total}}.', { total: base.pages })}
                  </p>
                )}
              </li>
            )
          })}
        </ul>
      )}
      {page?.next_cursor && (
        <Button
          className="mt-3"
          size="sm"
          variant="secondary"
          loading={loadingMore}
          onClick={() => void loadMore()}
        >
          {t('Load more')}
        </Button>
      )}
    </Dialog>
  )
}
