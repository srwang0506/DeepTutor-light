'use client'

import dynamic from 'next/dynamic'
import type { ReactNode } from 'react'

// Background reminders do not contribute to the server-rendered page. Load
// their transport and date helpers after hydration without delaying children.
const CalendarEffects = dynamic(() => import('./CalendarEffects'), { ssr: false })

export default function CalendarRuntime({ children }: { children: ReactNode }) {
  return (
    <>
      <CalendarEffects />
      {children}
    </>
  )
}
