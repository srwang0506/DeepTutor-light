'use client'
import { useTranslation } from 'react-i18next'
import Link from 'next/link'
import { ArrowUpRight, BookOpen, FlaskConical, Layers3, PenLine, Server } from 'lucide-react'
import { type ReactNode, useRef, useState } from 'react'
import { scopedUrl } from '@/lib/workspace-scope'
import './studio.css'

export function StudioShell({
  workspace,
  active,
  title,
  description,
  children,
  action,
}: {
  workspace: string
  active: string
  title: string
  description: string
  children: ReactNode
  action?: ReactNode
}) {
  const { t } = useTranslation()

  return (
    <section className="studio-page">
      <div className="studio-inner">
        <header className="studio-header">
          <div>
            <span className="studio-eyebrow">{t('PERSONAL STUDIO · 学习与科研')}</span>
            <h1>{title}</h1>
            <p>{description}</p>
          </div>
          {action}
        </header>
        <nav className="studio-nav" aria-label={t('研究工作界面')}>
          {[
            ['library', '/library', '资料库', BookOpen],
            ['evidence', '/studio/evidence', '证据与比较', Layers3],
            ['experiments', '/studio/experiments', '实验记录', FlaskConical],
            ['writing', '/studio/writing', '论文写作', PenLine],
            ['connections', '/studio/connections', '远程工作区', Server],
          ].map(([id, href, label, Icon]) => {
            const I = Icon as typeof BookOpen
            return (
              <Link
                key={id as string}
                href={scopedUrl(href as string, workspace)}
                aria-current={active === id ? 'page' : undefined}
              >
                <I size={15} />
                {label as string}
              </Link>
            )
          })}
        </nav>
        {children}
      </div>
    </section>
  )
}
export function StudioEmpty({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="studio-empty">
      <BookOpen size={28} />
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  )
}
export function Field({
  label,
  children,
  hint,
}: {
  label: string
  children: ReactNode
  hint?: string
}) {
  return (
    <label className="studio-field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  )
}
export function ErrorNotice({ message }: { message: string }) {
  return message ? (
    <p className="studio-error" role="alert">
      {message}
    </p>
  ) : null
}
export function Action({
  children,
  onClick,
  secondary = false,
  disabled = false,
}: {
  children: ReactNode
  onClick?: () => void
  secondary?: boolean
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      className={secondary ? 'studio-button secondary' : 'studio-button'}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  )
}
export function OutLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <Link className="studio-outlink" href={href}>
      {children}
      <ArrowUpRight size={13} />
    </Link>
  )
}
export function useOperation() {
  const lock = useRef(false)
  const [busy, setBusy] = useState(false),
    [error, setError] = useState('')
  async function run(fn: () => Promise<void>) {
    if (lock.current) return
    lock.current = true
    setBusy(true)
    setError('')
    try {
      await fn()
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作未完成，请重试。')
    } finally {
      lock.current = false
      setBusy(false)
    }
  }
  return { busy, error, setError, run }
}
