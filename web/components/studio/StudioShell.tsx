'use client'
import { useTranslation } from 'react-i18next'
import Link from 'next/link'
import { ArrowUpRight, BookOpen, CircleAlert } from 'lucide-react'
import { type ReactNode, useRef, useState } from 'react'
import { FeaturePage, PageHeader } from '@/components/layout/FeaturePage'
import { Button } from '@/shared/ui/Button'
import './studio.css'

export function StudioShell({
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
  return (
    <FeaturePage className="studio-page">
      <PageHeader title={title} description={description} action={action} />
      {children}
    </FeaturePage>
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
  const { t } = useTranslation()
  return message ? (
    <p className="studio-error" role="alert">
      <CircleAlert size={16} aria-hidden />
      <span>{t(message)}</span>
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
    <Button
      type="button"
      data-studio-action
      variant={secondary ? 'secondary' : 'primary'}
      className={secondary ? 'studio-button secondary' : 'studio-button'}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </Button>
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
  const { t } = useTranslation()
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
      setError(e instanceof Error ? e.message : t('操作未完成，请重试。'))
    } finally {
      lock.current = false
      setBusy(false)
    }
  }
  return { busy, error, setError, run }
}
