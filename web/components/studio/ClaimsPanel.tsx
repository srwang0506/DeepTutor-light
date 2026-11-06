'use client'
import { useTranslation } from 'react-i18next'
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { Plus } from 'lucide-react'
import { records, post, studio, type StudioRecord } from '@/lib/studio-api'
import { scopedUrl } from '@/lib/workspace-scope'
import { Dialog } from '@/shared/ui/Dialog'
import { Action, Field, ErrorNotice, StudioEmpty, useOperation } from './StudioShell'
type Source = { title?: string; label?: string; stance?: string }
type Edge = {
  source_id: string
  source_revision: number
  role: string
  qualifiers: string
  review: string
}
type Claim = { title: string; status: string; note: string; edges: Edge[] }
export default function ClaimsPanel({ workspace }: { workspace: string }) {
  const { t } = useTranslation()

  const op = useOperation(),
    [claims, setClaims] = useState<StudioRecord<Claim>[]>([]),
    [sources, setSources] = useState<StudioRecord<Source>[]>([]),
    [open, setOpen] = useState(false),
    [editing, setEditing] = useState<StudioRecord<Claim> | null>(null),
    [title, setTitle] = useState(''),
    [note, setNote] = useState(''),
    [status, setStatus] = useState('draft'),
    [edges, setEdges] = useState<Edge[]>([])
  async function reload() {
    const [c, e, o, a] = await Promise.all([
      records<Claim>(workspace, 'claim'),
      records<Source>(workspace, 'evidence'),
      records<Source>(workspace, 'observation'),
      records<Source>(workspace, 'artifact'),
    ])
    setClaims(c)
    setSources([...e, ...o, ...a])
  }
  useEffect(() => {
    const abort = new AbortController()
    Promise.all([
      records<Claim>(workspace, 'claim', abort.signal),
      records<Source>(workspace, 'evidence', abort.signal),
      records<Source>(workspace, 'observation', abort.signal),
      records<Source>(workspace, 'artifact', abort.signal),
    ])
      .then(([c, e, o, a]) => {
        setClaims(c)
        setSources([...e, ...o, ...a])
      })
      .catch((e) => {
        if (!abort.signal.aborted) op.setError(e.message)
      })
    return () => abort.abort()
  }, [workspace]) // eslint-disable-line react-hooks/exhaustive-deps
  function update(index: number, patch: Partial<Edge>) {
    setEdges((values) => values.map((edge, i) => (i === index ? { ...edge, ...patch } : edge)))
  }
  return (
    <>
      <div className="studio-toolbar">
        <p className="studio-note">
          {t('同一份材料可以支持一个限定结论，也可以反驳另一个概括。关系逐条核对，版本固定。')}
        </p>
        <Action
          onClick={() => {
            setTitle('')
            setNote('')
            setStatus('draft')
            setEdges([])
            setEditing(null)
            setOpen(true)
          }}
        >
          <Plus size={15} />
          {t('记录主张')}
        </Action>
      </div>
      <ErrorNotice message={open ? '' : op.error} />
      <div className="studio-card-list">
        {claims.map((c) => (
          <article key={c.id} className="studio-card">
            <span className={`studio-badge ${c.data.status === 'confirmed' ? 'good' : 'warn'}`}>
              {
                (
                  {
                    draft: '研究假设 / 待核实',
                    confirmed: '证据关系已核对',
                    disputed: '存在反证',
                  } as Record<string, string>
                )[c.data.status]
              }
            </span>
            <h3 style={{ marginTop: 12 }}>{c.data.title}</h3>
            <p>{c.data.note}</p>
            <Link
              className="studio-outlink"
              href={scopedUrl(`/objects/${c.id}?revision=${c.revision}`, workspace)}
            >
              {t('查看')}
              {c.data.edges?.length || 0} {t('条来源关系 ↗')}
            </Link>
            <button
              className="studio-link-button"
              style={{ marginLeft: 16 }}
              onClick={() => {
                setEditing(c)
                setTitle(c.data.title)
                setNote(c.data.note)
                setStatus(c.data.status)
                setEdges(c.data.edges || [])
                op.setError('')
                setOpen(true)
              }}
            >
              {t('继续核对')}
            </button>
          </article>
        ))}
      </div>
      {!claims.length && (
        <StudioEmpty title={t('给结论留出被检验的空间')}>
          {t('记录支持证据、相反解释与限定条件；未核实的想法可以先保存为假设。')}
        </StudioEmpty>
      )}
      <Dialog
        open={open}
        onClose={() => !op.busy && setOpen(false)}
        title={editing ? '更新主张与证据关系' : '记录主张与证据关系'}
        className="studio-dialog"
        size="lg"
        footer={
          <Action
            disabled={op.busy || !title.trim()}
            onClick={() =>
              op.run(async () => {
                const claim = { title, note, status, edges }
                if (editing)
                  await studio(workspace, `/claims/${editing.id}`, {
                    method: 'PUT',
                    body: JSON.stringify({ expected_revision: editing.revision, claim }),
                  })
                else await post(workspace, '/claims', claim)
                await reload()
                setOpen(false)
              })
            }
          >
            {t('保存主张')}
          </Action>
        }
      >
        <ErrorNotice message={op.error} />
        <Field label="要检验的具体主张">
          <textarea
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={t('写明适用数据、条件和范围，避免把局部结果写成普遍结论。')}
          />
        </Field>
        <Field label="状态">
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="draft">{t('假设 / 待核实')}</option>
            <option value="disputed">{t('存在反证')}</option>
            <option value="confirmed">{t('已核实')}</option>
          </select>
        </Field>
        {edges.map((edge, index) => (
          <div className="studio-panel" key={index} style={{ marginTop: 12 }}>
            <div className="studio-form-grid">
              <Field label="来源与冻结版本">
                <select
                  value={edge.source_id}
                  onChange={(e) => {
                    const source = sources.find((s) => s.id === e.target.value)
                    if (source)
                      update(index, {
                        source_id: source.id,
                        source_revision: source.revision,
                        role: source.data.stance || 'context',
                        review: 'unreviewed',
                      })
                  }}
                >
                  <option value="">{t('选择来源')}</option>
                  {sources.map((s) => (
                    <option value={s.id} key={s.id}>
                      {s.data.title || s.data.label} {t('· v')}
                      {s.revision}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="与这条主张的关系">
                <select
                  value={edge.role}
                  onChange={(e) => update(index, { role: e.target.value, review: 'unreviewed' })}
                >
                  {[
                    ['supports', '支持'],
                    ['contradicts', '反对'],
                    ['qualifies', '限定条件'],
                    ['context', '背景'],
                  ].map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <Field label="限定条件与核对理由">
              <textarea
                value={edge.qualifiers}
                onChange={(e) => update(index, { qualifiers: e.target.value })}
              />
            </Field>
            <label className="studio-check">
              <input
                type="checkbox"
                checked={edge.review === 'confirmed'}
                onChange={(e) =>
                  update(index, { review: e.target.checked ? 'confirmed' : 'unreviewed' })
                }
              />
              {t('我核对过这份来源与当前主张之间的关系')}
            </label>
            <button
              className="studio-link-button"
              onClick={() => setEdges((v) => v.filter((_, i) => i !== index))}
            >
              {t('移除这一关系')}
            </button>
          </div>
        ))}
        <Action
          secondary
          onClick={() =>
            setEdges((v) => [
              ...v,
              {
                source_id: '',
                source_revision: 1,
                role: 'context',
                qualifiers: '',
                review: 'unreviewed',
              },
            ])
          }
        >
          {t('添加来源关系')}
        </Action>
        <Field label="说明与相反解释">
          <textarea value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
      </Dialog>
    </>
  )
}
