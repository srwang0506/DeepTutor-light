'use client'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Dialog } from '@/shared/ui/Dialog'
import { Button } from '@/shared/ui/Button'
import { Field } from '@/shared/ui/Field'
import { InlineAlert } from '@/shared/ui/InlineAlert'
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
      <Button variant="ghost" size="sm" loading={busy && !open} onClick={() => void begin()}>
        {saved ? t('已复核题族') : t('复核目标与解法')}
      </Button>
      {error && !open && <InlineAlert tone="danger">{error}</InlineAlert>}
      <Dialog
        open={open}
        busy={busy}
        title={t('题族复核')}
        onClose={() => !busy && setOpen(false)}
        description={t(
          '进入复核会记为看过解法。相同题族需共享目标、解法与条件；同一段资料不代表同一题族。',
        )}
      >
        <div className="space-y-4">
          {error && <InlineAlert tone="danger">{error}</InlineAlert>}
          {candidates.map((c, i) => (
            <button
              className="block w-full rounded-xl border border-border bg-card p-3 text-left text-sm transition-colors hover:border-primary/40 hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
              <small className="mt-1 block text-xs text-muted-foreground">
                {t('候选建议，需你复核')} · {Math.round((c.score || 0) * 100)}%
              </small>
            </button>
          ))}
          {(['objective', 'method', 'conditions', 'rationale'] as const).map((key) => (
            <Field
              key={key}
              label={
                {
                  objective: t('目标概念'),
                  method: t('所需解法'),
                  conditions: t('成立条件与题型边界'),
                  rationale: t('为何属于这个题族'),
                }[key]
              }
            >
              <textarea
                className="block min-h-24 w-full resize-y rounded-xl border border-border bg-background px-3 py-2 text-sm leading-6 text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                required
                value={draft[key]}
                onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
              />
            </Field>
          ))}
          <div className="flex justify-end border-t border-border pt-4">
            <Button
              loading={busy}
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
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  )
}
