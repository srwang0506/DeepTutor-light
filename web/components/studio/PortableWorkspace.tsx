'use client'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { apiFetch } from '@/lib/api'
import { post, studio, studioUrl } from '@/lib/studio-api'
import { Action, ErrorNotice, Field, useOperation } from './StudioShell'
type Item = { resource: string; state: 'unchanged' | 'conflict' | 'new' | 'update' | 'delete' }
type Preview = { batch_id: string; state: string; items: Item[] }
type Transfer = { id: string; state: string; created: number }
export default function PortableWorkspace({ workspace }: { workspace: string }) {
  const { t } = useTranslation(),
    op = useOperation()
  const [loadError, setLoadError] = useState('')
  const [passphrase, setPassphrase] = useState(''),
    [file, setFile] = useState<File | null>(null),
    [preview, setPreview] = useState<Preview | null>(null),
    [choices, setChoices] = useState<Record<string, 'keep' | 'incoming'>>({}),
    [transfers, setTransfers] = useState<Transfer[]>([]),
    [message, setMessage] = useState('')
  useEffect(() => {
    const abort = new AbortController()
    void studio<Transfer[]>(workspace, '/portable/transfers', undefined, abort.signal)
      .then(setTransfers)
      .catch((e) => {
        if (!abort.signal.aborted) setLoadError(String(e))
      })
    return () => abort.abort()
  }, [workspace])
  async function exportProject() {
    await op.run(async () => {
      const res = await apiFetch(studioUrl('/portable/export', workspace), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ passphrase }),
      })
      if (!res.ok) throw new Error((await res.json()).detail)
      const url = URL.createObjectURL(await res.blob())
      const link = document.createElement('a')
      link.href = url
      link.download = 'project.dtsync'
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
      setMessage(t('已导出加密项目包。请在另一台设备的目标工作区导入。'))
    })
  }
  async function inspect() {
    if (!file) return
    await op.run(async () => {
      const body = new FormData()
      body.append('file', file)
      body.append('passphrase', passphrase)
      const result = await studio<Preview>(workspace, '/portable/preview', { method: 'POST', body })
      setPreview(result)
      setChoices({})
      setMessage('')
    })
  }
  async function apply() {
    if (!preview) return
    await op.run(async () => {
      const result = await post<{ unresolved_historical_edges?: unknown[] }>(
        workspace,
        '/portable/apply',
        { batch_id: preview.batch_id, choices },
      )
      setMessage(
        result.unresolved_historical_edges?.length
          ? t('导入完成；部分历史依赖需要重新核对，原始快照已保留。')
          : t('导入完成，回执已保存。'),
      )
      setPreview(null)
      setTransfers(await studio<Transfer[]>(workspace, '/portable/transfers'))
    })
  }
  const conflicts = preview?.items.filter((i) => i.state === 'conflict') || []
  return (
    <section className="rounded-2xl border border-[var(--border)] bg-[var(--card)] p-5 space-y-4">
      <div>
        <h2 className="font-semibold">{t('跨设备项目迁移')}</h2>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">
          {t(
            '用加密文件交换当前项目的聊天、学习记录、记忆、资料与科研成果。密钥和跨工作区记忆授权留在本机；导入前先检查差异。',
          )}
        </p>
      </div>
      <p className="studio-muted">{t('打包数据上限 200 MB，单文件 50 MB。请保存好传输口令。')}</p>
      <div className="grid gap-4 md:grid-cols-2">
        <Field label={t('传输口令（至少 12 个字符）')}>
          <input
            type="password"
            autoComplete="new-password"
            maxLength={256}
            value={passphrase}
            onChange={(e) => setPassphrase(e.target.value)}
          />
        </Field>
        <Field label={t('接收另一台设备的项目包')}>
          <input
            type="file"
            accept=".dtsync"
            onChange={(e) => {
              setFile(e.target.files?.[0] || null)
              setPreview(null)
            }}
          />
        </Field>
      </div>
      <div className="flex flex-wrap gap-2">
        <Action disabled={op.busy || passphrase.length < 12} onClick={() => void exportProject()}>
          {t('导出当前项目')}
        </Action>
        <Action
          disabled={op.busy || !file || passphrase.length < 12}
          onClick={() => void inspect()}
        >
          {t('预览导入')}
        </Action>
      </div>
      <ErrorNotice message={loadError || op.error} />
      {message && (
        <p role="status" className="text-sm">
          {message}
        </p>
      )}
      {preview && (
        <div className="space-y-3">
          <p className="text-sm">
            {t('{{count}} 项资源，{{conflicts}} 项冲突', {
              count: preview.items.length,
              conflicts: conflicts.length,
            })}
          </p>
          <div className="max-h-72 overflow-y-auto divide-y divide-[var(--border)]">
            {preview.items
              .filter((i) => i.state !== 'unchanged')
              .map((item) => (
                <div key={item.resource} className="py-2 flex flex-wrap items-center gap-3 text-sm">
                  <code className="min-w-0 flex-1 break-all">{item.resource}</code>
                  <span>
                    {
                      {
                        new: t('新增'),
                        update: t('更新'),
                        delete: t('来源已删除，保留可恢复记录'),
                        conflict: t('两端有差异'),
                        unchanged: t('无变化'),
                      }[item.state]
                    }
                  </span>
                  {item.state === 'conflict' && (
                    <select
                      aria-label={t('选择冲突处理方式')}
                      value={choices[item.resource] || ''}
                      onChange={(e) =>
                        setChoices({
                          ...choices,
                          [item.resource]: e.target.value as 'keep' | 'incoming',
                        })
                      }
                    >
                      <option value="" disabled>
                        {t('请选择')}
                      </option>
                      <option value="keep">{t('保留本机')}</option>
                      <option value="incoming">{t('采用导入内容')}</option>
                    </select>
                  )}
                </div>
              ))}
          </div>
          <Action
            disabled={
              op.busy || conflicts.some((i) => !choices[i.resource]) || preview.state === 'complete'
            }
            onClick={() => void apply()}
          >
            {t('确认导入此工作区')}
          </Action>
        </div>
      )}
      {transfers.length > 0 && (
        <details className="text-sm">
          <summary>{t('最近的传输回执')}</summary>
          {transfers.map((item) => (
            <div key={item.id} className="py-2">
              {new Date(item.created * 1000).toLocaleString()} ·{' '}
              {item.state === 'complete' ? t('已完成') : t('可重新上传原包恢复')} ·{' '}
              <code>{item.id.slice(0, 12)}</code>
            </div>
          ))}
        </details>
      )}
    </section>
  )
}
