'use client'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Dialog } from '@/shared/ui/Dialog'
import { apiFetch } from '@/lib/api'
import { randomUuid } from '@/lib/random-uuid'
interface Descriptor {
  objective: string
  method: string
  conditions: string
  rationale: string
  revision?: number
  score?: number
}
export default function QuestionFamilyReview({
  endpoint,
  identity,
  onDisclosure,
}: {
  endpoint: (path: string) => string
  identity: { session_id: string; turn_id: string; question_id: string; question_version: string }
  onDisclosure: () => void
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [saved, setSaved] = useState(false)
  const [draft, setDraft] = useState<Descriptor>({
      objective: '',
      method: '',
      conditions: '',
      rationale: '',
      revision: 0,
    }),
    [candidates, setCandidates] = useState<Descriptor[]>([])
  async function call(path: string, payload: unknown) {
    const response = await apiFetch(endpoint(path), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    const data = await response.json()
    if (!response.ok) throw new Error(data.detail || String(response.status))
    return data
  }
  async function begin() {
    setBusy(true)
    setError('')
    try {
      const data = await call('/families/suggest', {
        ...identity,
        level: 'revealed',
        event_id: randomUuid(),
      })
      onDisclosure()
      setCandidates(data.candidates)
      setDraft(
        data.current || { objective: '', method: '', conditions: '', rationale: '', revision: 0 },
      )
      setOpen(true)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  async function save() {
    setBusy(true)
    setError('')
    try {
      const data = await call('/families/review', {
        ...identity,
        objective: draft.objective,
        method: draft.method,
        conditions: draft.conditions,
        rationale: draft.rationale,
        expected_revision: draft.revision || 0,
      })
      setDraft(data)
      setSaved(true)
      setOpen(false)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="text-sm">
      <button
        type="button"
        className="px-3 py-2 text-[var(--primary)]"
        disabled={busy}
        onClick={() => void begin()}
      >
        {saved ? t('已复核题族') : t('复核目标与解法')}
      </button>
      {error && <p role="alert">{error}</p>}
      <Dialog
        open={open}
        title={t('题族复核')}
        onClose={() => !busy && setOpen(false)}
        description={t(
          '进入复核会记为看过解法。相同题族需共享目标、解法与条件；同一段资料不代表同一题族。',
        )}
      >
        <div className="space-y-4">
          {candidates.map((c, i) => (
            <button
              className="block rounded-lg border p-3 text-left w-full"
              key={i}
              type="button"
              onClick={() =>
                setDraft({
                  ...draft,
                  objective: c.objective,
                  method: c.method,
                  conditions: c.conditions,
                })
              }
            >
              {c.objective} · {c.method}
              <small className="block">
                {t('候选建议，需你复核')} · {Math.round((c.score || 0) * 100)}%
              </small>
            </button>
          ))}
          {(['objective', 'method', 'conditions', 'rationale'] as const).map((key) => (
            <label key={key} className="block">
              {
                {
                  objective: t('目标概念'),
                  method: t('所需解法'),
                  conditions: t('成立条件与题型边界'),
                  rationale: t('为何属于这个题族'),
                }[key]
              }
              <textarea
                className="mt-1 block w-full rounded-lg border bg-transparent p-2"
                required
                value={draft[key]}
                onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
              />
            </label>
          ))}
          <button
            className="rounded-lg bg-[var(--primary)] text-[var(--primary-foreground)] px-4 py-2"
            type="button"
            disabled={
              busy ||
              !draft.objective.trim() ||
              !draft.method.trim() ||
              !draft.conditions.trim() ||
              draft.rationale.trim().length < 5
            }
            onClick={() => void save()}
          >
            {t('确认并保存复核')}
          </button>
        </div>
      </Dialog>
    </div>
  )
}
