'use client'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { post, studio, type StudioRecord } from '@/lib/studio-api'
import { Action, ErrorNotice, useOperation } from './StudioShell'
interface Suite {
  id: string
  title: string
  digest: string
  cases: number
}
interface Run {
  id: string
  state: string
  completed: number
  total: number
  model: { model: string }
}
interface Metric {
  cases: number
  finished: number
  correct: number
  accuracy_over_all_cases: number
  coverage: number
  answerable_coverage: number | null
  estimated_tokens: number
  invalid_citations: number
  citation_hits: number
  citation_expected: number
  pending_human_reviews: number
  reported_tokens: number | null
  elapsed_seconds: number
}
interface Report {
  run_id: string
  state: string
  metrics: Record<string, Metric>
  suite_digest: string
  authorship: string
  model: { model: string }
  perturbation_regressions: number | null
  independent_certification: boolean
}
export default function EvaluationPanel({ workspace }: { workspace: string }) {
  const { t } = useTranslation(),
    op = useOperation()
  const [suites, setSuites] = useState<Suite[]>([]),
    [selected, setSelected] = useState(''),
    [runs, setRuns] = useState<Run[]>([]),
    [report, setReport] = useState<Report | null>(null),
    [running, setRunning] = useState(false),
    [loadError, setLoadError] = useState(''),
    [file, setFile] = useState<File | null>(null)
  const stop = useRef(false)
  useEffect(() => {
    const controller = new AbortController()
    void Promise.all([
      studio<Suite[]>(workspace, '/evaluations/suites', undefined, controller.signal),
      studio<Run[]>(workspace, '/evaluations/runs', undefined, controller.signal),
    ])
      .then(([s, r]) => {
        setSuites(s)
        setRuns(r)
      })
      .catch((e) => {
        if (!controller.signal.aborted) setLoadError(String(e))
      })
    return () => {
      stop.current = true
      controller.abort()
    }
  }, [workspace])
  async function upload() {
    if (!file) return
    await op.run(async () => {
      const body = new FormData()
      body.append('file', file)
      const suite = await studio<Suite>(workspace, '/evaluations/suites', { method: 'POST', body })
      setSuites((old) => [suite, ...old])
      setSelected(suite.id)
    })
  }
  async function evaluate(id?: string) {
    stop.current = false
    setRunning(true)
    try {
      await op.run(async () => {
        const run = id
          ? { id }
          : await post<StudioRecord<unknown>>(workspace, '/evaluations/runs', {
              suite_id: selected,
            })
        let current: Report
        do {
          current = await post<Report>(workspace, `/evaluations/runs/${run.id}/next`, {})
          setReport(current)
        } while (current.state !== 'complete' && !stop.current)
        setRuns(await studio<Run[]>(workspace, '/evaluations/runs'))
      })
    } finally {
      setRunning(false)
    }
  }
  function download() {
    if (!report) return
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }),
    )
    const a = document.createElement('a')
    a.href = url
    a.download = `evaluation-${report.run_id}.json`
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  return (
    <details className="studio-card">
      <summary className="cursor-pointer font-semibold">{t('质量评测与复核')}</summary>
      <div className="space-y-4 pt-4">
        <p className="studio-muted">
          {t(
            '先导入并冻结独立整理的任务集，再使用当前模型逐题执行。标准答案不会发给模型；同一题族或论文不能跨开发集和测试集。开放题保留人工复核状态。',
          )}
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <input
            type="file"
            accept="application/json,.json"
            aria-label={t('评测任务集 JSON')}
            disabled={running}
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
          <Action secondary disabled={!file || op.busy || running} onClick={() => void upload()}>
            {t('冻结任务集')}
          </Action>
        </div>
        <div className="flex flex-wrap gap-3">
          <select
            aria-label={t('选择已冻结的任务集')}
            value={selected}
            disabled={running}
            onChange={(e) => setSelected(e.target.value)}
          >
            <option value="">{t('选择任务集')}</option>
            {suites.map((s) => (
              <option key={s.id} value={s.id}>
                {s.title} · {s.cases}
              </option>
            ))}
          </select>
          <Action disabled={!selected || running || op.busy} onClick={() => void evaluate()}>
            {t('使用当前模型开始评测')}
          </Action>
          {running && (
            <Action
              secondary
              onClick={() => {
                stop.current = true
              }}
            >
              {t('完成当前题后暂停')}
            </Action>
          )}
        </div>
        <ErrorNotice message={op.error || loadError} />
        {report && (
          <section className="space-y-3" aria-live="polite">
            <p>
              {report.model.model} · {report.metrics.all.finished}/{report.metrics.all.cases} ·{' '}
              {report.state === 'complete' ? t('运行完成') : t('可以继续')}
            </p>
            <div className="overflow-x-auto">
              <table className="studio-table">
                <thead>
                  <tr>
                    <th>{t('领域')}</th>
                    <th>{t('全部任务准确率')}</th>
                    <th>{t('可答任务覆盖率')}</th>
                    <th>{t('无效引用')}</th>
                    <th>{t('待人工复核')}</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(report.metrics).map(([domain, m]) => (
                    <tr key={domain}>
                      <td>{domain === 'all' ? t('合计') : domain}</td>
                      <td>{Math.round(m.accuracy_over_all_cases * 100)}%</td>
                      <td>
                        {m.answerable_coverage === null
                          ? '—'
                          : `${Math.round(m.answerable_coverage * 100)}%`}
                      </td>
                      <td>{m.invalid_citations}</td>
                      <td>{m.pending_human_reviews}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="studio-muted">
              {t(
                '自动评分只覆盖精确答案和来源位置。独立作者声明不等于独立认证；未知费用不会记为零。',
              )}
            </p>
            <p className="studio-muted">
              {t('调用耗时')} {report.metrics.all.elapsed_seconds.toFixed(1)}
              {' s · '}
              {t('服务方报告的 token')}{' '}
              {report.metrics.all.reported_tokens?.toLocaleString() ?? t('Unknown')} ·{' '}
              {t('估算 token')} {report.metrics.all.estimated_tokens.toLocaleString()} ·{' '}
              {t('费用未知')}
            </p>
            <Action secondary onClick={download}>
              {t('导出完整评测回执')}
            </Action>
          </section>
        )}
        {runs.length > 0 && (
          <div className="space-y-2">
            {runs.map((run) => (
              <div key={run.id} className="flex items-center justify-between gap-3 text-sm">
                <span>
                  {run.model.model} · {run.completed}/{run.total}
                </span>
                <div className="flex gap-2">
                  <Action
                    secondary
                    disabled={running}
                    onClick={() =>
                      void op.run(async () =>
                        setReport(await studio<Report>(workspace, `/evaluations/runs/${run.id}`)),
                      )
                    }
                  >
                    {t('查看回执')}
                  </Action>
                  {run.state !== 'complete' && (
                    <Action disabled={running} onClick={() => void evaluate(run.id)}>
                      {t('继续评测')}
                    </Action>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </details>
  )
}
