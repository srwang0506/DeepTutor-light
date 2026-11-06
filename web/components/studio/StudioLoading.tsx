'use client'
import { useTranslation } from 'react-i18next'
export default function StudioLoading({ label }: { label: string }) {
  const { t } = useTranslation()
  return (
    <p className="studio-note" role="status">
      {t(label)}
    </p>
  )
}
