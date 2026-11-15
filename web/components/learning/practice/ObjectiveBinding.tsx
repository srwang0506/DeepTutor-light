'use client'

import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link2 } from 'lucide-react'
import {
  getPracticeObjectives,
  setPracticeObjective,
  type PracticeObjective,
  type PracticeObjectivePath,
  type PracticeQuestionState,
} from '@/lib/practice-api'

export function ObjectiveBinding({
  id,
  state,
  original,
  workspace,
  disabled,
  onSaved,
  onBusy,
  onConflict,
}: {
  id: number
  state: PracticeQuestionState
  original: boolean
  workspace?: string
  disabled: boolean
  onSaved: (state: PracticeQuestionState) => void
  onBusy: (busy: boolean) => void
  onConflict?: () => void
}) {
  const { t } = useTranslation()
  const baseId = useId()
  const [open, setOpen] = useState(false)
  const [paths, setPaths] = useState<PracticeObjectivePath[] | null>(null)
  const [pathId, setPathId] = useState('')
  const [pointId, setPointId] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const selectedPath = paths?.find((path) => path.path_id === pathId)
  async function choose() {
    setOpen(true)
    setPathId(state.objective?.path_id ?? '')
    setPointId(state.objective?.knowledge_point_id ?? '')
    setError('')
    setLoading(true)
    try {
      setPaths((await getPracticeObjectives(workspace)).paths)
    } catch {
      setError('Could not load learning objectives. Try again.')
    } finally {
      setLoading(false)
    }
  }
  async function save(objective: PracticeObjective | null) {
    if (disabled) return
    setError('')
    onBusy(true)
    try {
      const result = await setPracticeObjective(id, state.version, objective, workspace)
      onSaved(result.state)
      setOpen(false)
    } catch (err) {
      if (err instanceof Error && 'status' in err && err.status === 409) onConflict?.()
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      onBusy(false)
    }
  }
  if (original) {
    return (
      <p className="mb-4 flex items-center gap-2 text-xs text-muted-foreground">
        <Link2 size={14} aria-hidden /> {t('Linked to its original learning objective')}
      </p>
    )
  }
  if (!open) {
    return (
      <button
        type="button"
        disabled={disabled}
        onClick={() => void choose()}
        className="mb-4 inline-flex items-center gap-2 text-xs font-medium text-primary hover:underline disabled:opacity-50"
      >
        <Link2 size={14} aria-hidden />
        {t(state.objective ? 'Change linked objective' : 'Link a learning objective')}
      </button>
    )
  }
  return (
    <section
      aria-label={t('Link a learning objective')}
      className="mb-4 space-y-3 rounded-xl border border-border bg-muted/30 p-4 text-sm"
    >
      <p className="text-xs text-muted-foreground">
        {t(
          'Link only if this question checks the selected objective. The link is used for future independent recalls.',
        )}
      </p>
      {loading && <p role="status">{t('Loading…')}</p>}
      {error && (
        <p role="alert" className="text-destructive">
          {t(error)}
        </p>
      )}
      {!loading && paths?.length === 0 && (
        <p>{t('There are no learning objectives in this workspace yet.')}</p>
      )}
      {!loading && !!paths?.length && (
        <div className="grid gap-3 sm:grid-cols-2">
          <label htmlFor={`${baseId}-path`} className="grid gap-1.5 text-xs font-medium">
            {t('Learning path')}
            <select
              id={`${baseId}-path`}
              value={selectedPath ? pathId : ''}
              disabled={disabled}
              onChange={(event) => {
                setPathId(event.target.value)
                setPointId('')
              }}
              className="w-full rounded-lg border border-border bg-background p-2 text-sm"
            >
              <option value="">{t('Choose a learning path')}</option>
              {paths.map((path) => (
                <option key={path.path_id} value={path.path_id}>
                  {path.name}
                </option>
              ))}
            </select>
          </label>
          <label htmlFor={`${baseId}-point`} className="grid gap-1.5 text-xs font-medium">
            {t('Learning objective')}
            <select
              id={`${baseId}-point`}
              value={selectedPath?.objectives.some(({ id }) => id === pointId) ? pointId : ''}
              disabled={disabled || !selectedPath}
              onChange={(event) => setPointId(event.target.value)}
              className="w-full rounded-lg border border-border bg-background p-2 text-sm"
            >
              <option value="">{t('Choose an objective')}</option>
              {selectedPath?.objectives.map((point) => (
                <option key={point.id} value={point.id}>
                  {point.name}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}
      <div className="flex flex-wrap justify-end gap-2">
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="rounded-lg px-3 py-2 text-xs hover:bg-muted"
        >
          {t('Cancel')}
        </button>
        {!!state.objective && (
          <button
            type="button"
            disabled={disabled}
            onClick={() => void save(null)}
            className="rounded-lg border border-border px-3 py-2 text-xs disabled:opacity-40"
          >
            {t('Remove objective link')}
          </button>
        )}
        {paths === null && !loading && (
          <button
            type="button"
            onClick={() => void choose()}
            className="rounded-lg border border-border px-3 py-2 text-xs"
          >
            {t('Retry')}
          </button>
        )}
        {!!paths?.length && (
          <button
            type="button"
            disabled={disabled || !selectedPath?.objectives.some(({ id }) => id === pointId)}
            onClick={() => void save({ path_id: pathId, knowledge_point_id: pointId })}
            className="rounded-lg bg-primary px-3 py-2 text-xs text-primary-foreground disabled:opacity-40"
          >
            {t('Confirm objective link')}
          </button>
        )}
      </div>
    </section>
  )
}
