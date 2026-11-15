'use client'

import { useSyncExternalStore } from 'react'
import { RECOVERY_PRINCIPAL_CHANGED, recoveryPrincipal } from '@/lib/recovery-principal'

function subscribe(onChange: () => void) {
  window.addEventListener(RECOVERY_PRINCIPAL_CHANGED, onChange)
  return () => window.removeEventListener(RECOVERY_PRINCIPAL_CHANGED, onChange)
}

export function useRecoveryPrincipal() {
  return useSyncExternalStore(subscribe, recoveryPrincipal, () => null)
}
