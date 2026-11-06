'use client'

import { forwardRef, useState, type InputHTMLAttributes } from 'react'
import { useTranslation } from 'react-i18next'
import { cn } from './styles'

/** A native file picker with labels that follow the interface language. */
export const FileInput = forwardRef<HTMLInputElement, Omit<InputHTMLAttributes<HTMLInputElement>, 'type'>>(
  function FileInput({ onChange, className, disabled, style, ...props }, ref) {
    const { t } = useTranslation()
    const [names, setNames] = useState<string[]>([])
    return (
      <span
        className={cn(
          'relative flex min-h-[42px] w-full min-w-0 items-center gap-3 overflow-hidden rounded-[10px] border border-border bg-card px-3 py-2 text-[13px] font-normal text-foreground',
          'focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-2 focus-within:ring-offset-background',
          disabled && 'opacity-60',
          className,
        )}
      >
        <input
          {...props}
          ref={ref}
          type="file"
          disabled={disabled}
          aria-label={props['aria-label']}
          title={names.length ? names.join(', ') : t('No file selected')}
          style={{ ...style, position: 'absolute', inset: 0, width: '100%', height: '100%', opacity: 0, cursor: disabled ? 'not-allowed' : 'pointer' }}
          onChange={(event) => {
            onChange?.(event)
            // Callers may clear a rejected file or reset the picker for another upload.
            setNames(Array.from(event.currentTarget.files || [], (file) => file.name))
          }}
        />
        <span aria-hidden className="shrink-0 rounded-md bg-secondary px-2 py-1 font-medium text-secondary-foreground">
          {t('Choose file')}
        </span>
        <span aria-hidden className="min-w-0 truncate text-muted-foreground">
          {names.length ? names.join(', ') : t('No file selected')}
        </span>
      </span>
    )
  },
)

export default FileInput
