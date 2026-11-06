'use client'

import { useState } from 'react'
import { Download } from 'lucide-react'
import { Dialog } from '@/shared/ui/Dialog'
import { post, type Work } from '@/lib/studio-api'
import { Action, ErrorNotice, Field, useOperation } from './StudioShell'

interface ExportReport {
  content: string
  filename: string
  mime: string
  lossless: boolean
  losses: { edition_id: string; from: string; to: string; fields_requiring_review: string[] }[]
}

export default function BibliographyExport({
  workspace,
  works,
}: {
  workspace: string
  works: Work[]
}) {
  const [open, setOpen] = useState(false)
  const [selected, setSelected] = useState<string[]>([])
  const [format, setFormat] = useState('bibtex')
  const [report, setReport] = useState<ExportReport | null>(null)
  const op = useOperation()
  const editions = works.flatMap(work =>
    work.editions.map(edition => ({
      id: edition.id,
      label: `${work.data.title} · ${edition.data.label}`,
    }))
  )
  function download() {
    if (!report) return
    const url = URL.createObjectURL(new Blob([report.content], { type: report.mime }))
    const link = document.createElement('a')
    link.href = url
    link.download = report.filename
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  return (
    <>
      <Action
        secondary
        disabled={!editions.length}
        onClick={() => {
          setSelected(editions.map(item => item.id))
          setReport(null)
          op.setError('')
          setOpen(true)
        }}
      >
        <Download size={14} />
        导出书目
      </Action>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="带走你的文献积累"
        size="lg"
        className="studio-dialog"
        busy={op.busy}
        footer={
          <div className="studio-actions">
            <Action secondary onClick={() => setOpen(false)}>
              关闭
            </Action>
            {report ? (
              <Action onClick={download}>
                <Download size={14} />
                下载 {report.filename}
              </Action>
            ) : (
              <Action
                disabled={op.busy || !selected.length}
                onClick={() =>
                  op.run(async () => {
                    setReport(
                      await post<ExportReport>(workspace, '/bibliography/export-report', {
                        edition_ids: selected,
                        format,
                      })
                    )
                  })
                }
              >
                {op.busy ? '正在检查…' : '检查并准备导出'}
              </Action>
            )}
          </div>
        }
      >
        <ErrorNotice message={op.error} />
        <p className="studio-note">
          从当前已加载的资料中选择版本。导出前检查转换差异，原始条目会继续保留。
        </p>
        <Field label="文件格式">
          <select
            value={format}
            onChange={e => {
              setFormat(e.target.value)
              setReport(null)
            }}
          >
            <option value="bibtex">BibTeX</option>
            <option value="ris">原始 RIS</option>
            <option value="csl">原始 CSL JSON</option>
          </select>
        </Field>
        <div className="studio-source-picker">
          {editions.map(item => (
            <label className="studio-outline-row" key={item.id}>
              <input
                type="checkbox"
                checked={selected.includes(item.id)}
                onChange={e => {
                  setSelected(ids =>
                    e.target.checked ? [...ids, item.id] : ids.filter(id => id !== item.id)
                  )
                  setReport(null)
                }}
              />{' '}
              {item.label}
            </label>
          ))}
        </div>
        {report && (
          <section className="studio-panel" role="status">
            <h3>{report.lossless ? '已保留原始条目字段' : '格式转换需要核对'}</h3>
            <p className="studio-note">
              {report.lossless
                ? '原有文献类型与字段一并导出。'
                : '下列来源格式与目标不同；部分字段可能没有等价表示。请核对后下载。'}
            </p>
            {report.losses.map(loss => (
              <p key={loss.edition_id} className="studio-note">
                {editions.find(item => item.id === loss.edition_id)?.label}：{loss.from} → {loss.to}
                <br />
                待核对字段：{loss.fields_requiring_review.join('、')}
              </p>
            ))}
            <details>
              <summary>预览导出内容</summary>
              <pre className="studio-diff">{report.content}</pre>
            </details>
          </section>
        )}
      </Dialog>
    </>
  )
}
