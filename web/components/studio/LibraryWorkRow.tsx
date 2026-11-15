'use client'

import { useId, useState } from 'react'
import Link from 'next/link'
import { FileText, MoreHorizontal } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { IconButton } from '@/shared/ui/IconButton'
import { useMenuPopover } from '@/shared/ui/useMenuPopover'
import { scopedUrl } from '@/lib/workspace-scope'
import type { Edition, Work } from '@/lib/studio-api'

interface LibraryWorkRowProps {
  work: Work
  workspace: string
  busy: boolean
  onAttach: (edition: Edition) => void
  onAddEdition: () => void
  onJoin: (edition: Edition) => void
}

export default function LibraryWorkRow({
  work, workspace, busy, onAttach, onAddEdition, onJoin,
}: LibraryWorkRowProps) {
  const { t } = useTranslation()
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useMenuPopover<HTMLDivElement>(menuOpen, setMenuOpen)
  const menuId = useId()
  const single = work.editions.length === 1 ? work.editions[0] : null
  const metadata = [work.data.authors.slice(0, 4).join(' · '), work.data.year].filter(Boolean).join(' · ')
  const readUrl = (edition: Edition) => scopedUrl(`/library/${edition.id}`, workspace)
  const closeMenu = () => {
    setMenuOpen(false)
    menuRef.current?.querySelector<HTMLButtonElement>('[aria-haspopup="menu"]')?.focus()
  }

  return (
    <article className="library-work">
      <div className="library-work-row">
        <FileText size={16} strokeWidth={1.7} className="library-work-icon" aria-hidden />
        <div className="library-work-content">
          <h2 className="library-work-title">
            {single?.data.files.length ? <Link href={readUrl(single)}>{work.data.title}</Link> : work.data.title}
          </h2>
          {metadata && <p className="library-work-meta">{metadata}</p>}
          {!!work.data.tags.length && <p className="library-work-tags">{work.data.tags.join(' · ')}</p>}
          {single && (
            <>
              <p className="library-work-status">
                {single.data.label && <span>{single.data.label}</span>}
                <span>{single.data.files.length ? t('已存原文') : t('待补全文')}</span>
                {single.project_role && <span>{t('本项目')}</span>}
              </p>
              <BibliographicDetails edition={single} />
            </>
          )}
        </div>
        <div className="library-work-actions">
          {single && (
            <EditionAction edition={single} workspace={workspace} busy={busy} onAttach={onAttach} />
          )}
          <div
            ref={menuRef}
            className="library-work-menu-wrap"
            onBlur={event => {
              if (!event.currentTarget.contains(event.relatedTarget)) setMenuOpen(false)
            }}
          >
            <IconButton
              label={t('更多操作')}
              icon={<MoreHorizontal size={16} strokeWidth={1.7} aria-hidden />}
              size="sm"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              aria-controls={menuOpen ? menuId : undefined}
              disabled={busy}
              onClick={() => setMenuOpen(open => !open)}
            />
            {menuOpen && (
              <div id={menuId} role="menu" aria-label={t('更多操作')} className="library-work-menu">
                {single && !single.project_role && (
                  <button type="button" role="menuitem" className="library-work-menu-item" disabled={busy}
                    onClick={() => { closeMenu(); onJoin(single) }}>
                    {t('加入项目')}
                  </button>
                )}
                <button type="button" role="menuitem" className="library-work-menu-item" disabled={busy}
                  onClick={() => { closeMenu(); onAddEdition() }}>
                  {t('添加版本')}
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
      {work.editions.length > 1 && (
        <details className="library-work-versions">
          <summary>{t('{{count}} 个版本', { count: work.editions.length })}</summary>
          <div className="library-work-editions">
            {work.editions.map(edition => (
              <section className="library-work-edition" key={edition.id}>
                <div className="library-work-edition-content">
                  <h3 className="library-work-edition-title">
                    {edition.data.files.length ? <Link href={readUrl(edition)}>{edition.data.label}</Link> : edition.data.label}
                  </h3>
                  <p className="library-work-status">
                    <span>{edition.data.files.length ? t('已存原文') : t('待补全文')}</span>
                    {edition.project_role && <span>{t('本项目')}</span>}
                  </p>
                  <BibliographicDetails edition={edition} />
                </div>
                <div className="library-work-edition-actions">
                  <EditionAction edition={edition} workspace={workspace} busy={busy} onAttach={onAttach} />
                  {!edition.project_role && (
                    <button type="button" className="library-work-link" disabled={busy} onClick={() => onJoin(edition)}>
                      {t('加入项目')}
                    </button>
                  )}
                </div>
              </section>
            ))}
          </div>
        </details>
      )}
    </article>
  )
}

function EditionAction({ edition, workspace, busy, onAttach }: {
  edition: Edition
  workspace: string
  busy: boolean
  onAttach: (edition: Edition) => void
}) {
  const { t } = useTranslation()
  return edition.data.files.length ? (
    <Link className="library-work-link" href={scopedUrl(`/library/${edition.id}`, workspace)}>{t('阅读')}</Link>
  ) : (
    <button type="button" className="library-work-link" disabled={busy} onClick={() => onAttach(edition)}>
      {t('添加原文')}
    </button>
  )
}

function BibliographicDetails({ edition }: { edition: Edition }) {
  const { t } = useTranslation()
  const sources = edition.data.bibliographic_sources || []
  const conflicts = edition.data.bibliographic_conflicts || []
  const keys = [...new Set([edition.data.citekey, ...(edition.data.citekey_aliases || [])].filter(Boolean))]
  if (!sources.length && !conflicts.length && !keys.length && !edition.data.doi && !edition.data.arxiv) return null

  return (
    <details className="library-work-bibliography">
      <summary>
        {t('书目信息')}
        {!!conflicts.length && <span className="library-work-conflict-count">{t('{{count}} 项字段冲突', { count: conflicts.length })}</span>}
      </summary>
      {!!keys.length && <p className="library-work-citation-keys">{t('引用键：')}{keys.join(' · ')}</p>}
      {edition.data.doi && <p>{t('DOI')}: {edition.data.doi}</p>}
      {edition.data.arxiv && <p>{t('arXiv')}: {edition.data.arxiv}</p>}
      {!!sources.length && (
        <div className="library-work-sources">
          {sources.map((source, index) => (
            <details key={index} className="library-work-source">
              <summary>{t('导入来源')} · {source.format}</summary>
              <pre>{JSON.stringify(source.record, null, 2)}</pre>
            </details>
          ))}
        </div>
      )}
      {!!conflicts.length && (
        <dl className="library-work-conflicts">
          {conflicts.map((conflict, index) => (
            <div key={index}>
              <dt>{conflict.field}</dt>
              <dd>{t('保留值：')}{displayValue(conflict.retained)}</dd>
              <dd>{t('导入值：')}{displayValue(conflict.incoming)}</dd>
            </div>
          ))}
        </dl>
      )}
    </details>
  )
}

function displayValue(value: unknown): string {
  return typeof value === 'string' ? value : (JSON.stringify(value) ?? '')
}
