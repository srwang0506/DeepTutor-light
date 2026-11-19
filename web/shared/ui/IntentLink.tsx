'use client'

import Link from 'next/link'
import { useEffect, useRef, useState, type ComponentProps } from 'react'

type Props = Omit<ComponentProps<typeof Link>, 'href' | 'prefetch'> & { href: string }

/** Keep lists inexpensive to display. A deliberate hover or keyboard focus warms one destination. */
export default function IntentLink({ href, onPointerEnter, onPointerLeave, onFocus, onBlur, ...props }: Props) {
  const [intent, setIntent] = useState('')
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const cancel = () => clearTimeout(timer.current)
  useEffect(() => () => clearTimeout(timer.current), [href])
  return (
    <Link {...props} href={href} prefetch={intent === href ? null : false}
      onPointerEnter={event => {
        onPointerEnter?.(event)
        if (!event.defaultPrevented && event.pointerType === 'mouse' && props['aria-current'] !== 'page') {
          cancel()
          timer.current = setTimeout(() => setIntent(href), 120)
        }
      }}
      onPointerLeave={event => {
        onPointerLeave?.(event)
        cancel()
        if (event.currentTarget !== document.activeElement) setIntent('')
      }}
      onFocus={event => {
        onFocus?.(event)
        if (!event.defaultPrevented && props['aria-current'] !== 'page') setIntent(href)
      }}
      onBlur={event => {
        onBlur?.(event)
        cancel()
        setIntent('')
      }}
    />
  )
}
