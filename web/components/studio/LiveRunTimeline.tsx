'use client'
import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useKeyedState } from '@/hooks/useKeyedState'
import { studio } from '@/lib/studio-api'
import { Action, ErrorNotice } from './StudioShell'
interface Event {
  event_id: string
  type: string
  name?: string
  value?: number
  step?: number
  [key: string]: unknown
}
export default function LiveRunTimeline({
  workspace,
  runId,
}: {
  workspace: string
  runId: string
}) {
  const { t } = useTranslation()
  const [before, setBefore] = useKeyedState(`${workspace}:${runId}`, '')
  const resourceKey = JSON.stringify([workspace, runId, before])
  const [window, setWindow] = useKeyedState<{ events: Event[]; older: string }>(resourceKey, {
    events: [],
    older: '',
  })
  const { events, older } = window
  const [error, setError] = useKeyedState(resourceKey, '')
  useEffect(() => {
    let active = true,
      cursor = 'latest',
      inflight = false
    const load = async () => {
      if (inflight) return
      inflight = true
      try {
        let more = true
        let pages = 0
        while (active && more && pages++ < 4) {
          const page = await studio<{
            events: Event[]
            cursor: string
            first_cursor: string
            has_more: boolean
          }>(workspace, `/runs/${runId}/events?after=${cursor}&before=${before || 0}`)
          if (!active) return
          const first = cursor === 'latest'
          cursor = page.cursor
          setWindow((old) => {
            const all = [
              ...new Map([...old.events, ...page.events].map((e) => [e.event_id, e])).values(),
            ]
            const kept = all.slice(-2000)
            return {
              events: kept,
              older: first
                ? page.has_more
                  ? page.first_cursor
                  : ''
                : all.length > 2000
                  ? String(kept[0]._cursor)
                  : old.older,
            }
          })
          more = !first && !before && page.has_more
        }
        if (active) setError('')
      } catch (e) {
        if (active) setError(String(e))
      } finally {
        inflight = false
      }
    }
    void load()
    const timer = before ? null : setInterval(load, 10000)
    return () => {
      active = false
      if (timer) clearInterval(timer)
    }
  }, [workspace, runId, before, setWindow, setError])
  const names = [...new Set(events.filter((e) => e.type === 'metric').map((e) => e.name!))]
  if (!events.length && !error && !before) return null
  return (
    <section aria-label={t('实时指标历史')} className="studio-timeline">
      <h3>{t('实时指标历史')}</h3>
      <p className="studio-muted">
        {t('曲线来自来源日志的声明，不代表本系统见证了训练执行。协议与单位仍需复核。')}
      </p>
      <p className="studio-muted">{t('当前窗口最多保留 2000 条事件；更早记录可分页查看。')}</p>
      <div className="studio-actions">
        {older && (
          <Action secondary onClick={() => setBefore(older)}>
            {t('更早事件')}
          </Action>
        )}
        {before && (
          <Action secondary onClick={() => setBefore('')}>
            {t('返回实时')}
          </Action>
        )}
      </div>
      <ErrorNotice message={error} />
      {names.map((name) => {
        const samples = [
          ...new Map(
            events.filter((e) => e.type === 'metric' && e.name === name).map((e) => [e.step!, e]),
          ).values(),
        ].sort((a, b) => a.step! - b.step!)
        const xs = samples.map((e) => e.step!),
          ys = samples.map((e) => e.value!)
        const xmin = xs.reduce((a, b) => Math.min(a, b), Infinity),
          xmax = xs.reduce((a, b) => Math.max(a, b), -Infinity),
          ymin = ys.reduce((a, b) => Math.min(a, b), Infinity),
          ymax = ys.reduce((a, b) => Math.max(a, b), -Infinity)
        const points = samples
          .map(
            (e) =>
              `${20 + ((e.step! - xmin) / (xmax - xmin || 1)) * 460},${140 - ((e.value! - ymin) / (ymax - ymin || 1)) * 120}`,
          )
          .join(' ')
        return (
          <figure key={name} className="studio-metric">
            <figcaption>
              <strong>{name}</strong>
              <span>
                {t('最新值')} {samples.at(-1)?.value}
              </span>
              <span>
                {t('步数')} {samples.at(-1)?.step}
              </span>
            </figcaption>
            <svg viewBox="0 0 500 165" role="img" aria-label={`${name}: ${ymin} – ${ymax}`}>
              <path d="M20 20V140H480" fill="none" stroke="currentColor" opacity=".2" />
              <polyline points={points} fill="none" stroke="var(--studio-ink)" strokeWidth="2" />
              <text x="20" y="160" fontSize="10">
                {xmin}
              </text>
              <text x="455" y="160" fontSize="10">
                {xmax}
              </text>
            </svg>
            <details>
              <summary>{t('原始样本')}</summary>
              <div className="studio-table-wrap">
                <table className="studio-table">
                  <thead>
                    <tr>
                      <th>{t('步数')}</th>
                      <th>{t('数值')}</th>
                      <th>{t('事件')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {samples.map((e) => (
                      <tr key={e.event_id}>
                        <td>{e.step}</td>
                        <td>{e.value}</td>
                        <td>{e.event_id}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          </figure>
        )
      })}
      <details>
        <summary>{t('运行与产物声明')}</summary>
        <pre className="studio-diff">
          {JSON.stringify(
            events.filter((e) => e.type !== 'metric'),
            null,
            2,
          )}
        </pre>
      </details>
    </section>
  )
}
