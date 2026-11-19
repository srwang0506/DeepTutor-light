'use client'
import { useTranslation } from 'react-i18next'
import { studioLabel } from './display'
import { useState } from 'react'
import { Sparkles } from 'lucide-react'
import { Dialog } from '@/shared/ui/Dialog'
import ModelSelector from '@/components/chat/home/ModelSelector'
import { listLLMOptions, type LLMOptionsResponse } from '@/lib/llm-options'
import type { LLMSelection } from '@/features/chat/model/protocol'
import { studio, post, records, type StudioRecord, type ProposalData } from '@/lib/studio-api'
import { Action, Field, ErrorNotice, useOperation } from './StudioShell'
import type { TextSelection } from './TexEditor'
type Preferences = { allow_cloud: boolean; terminology: string; style_examples: string }
type Source = StudioRecord<{
  title?: string
  label?: string
  quote?: string
  verification?: string
  status?: string
}>
export default function WritingAssistant({
  workspace,
  manuscript,
  file,
  selection,
  flush,
  proposed,
}: {
  workspace: string
  manuscript: string
  file: { path: string; hash: string; text: string } | null
  selection: TextSelection
  flush: () => Promise<void>
  proposed: (row: StudioRecord<ProposalData>) => Promise<void>
}) {
  const { t, i18n } = useTranslation()

  const [open, setOpen] = useState(false),
    [mode, setMode] = useState('polish'),
    [instruction, setInstruction] = useState(''),
    [section, setSection] = useState('paragraph-flow')
  const [pref, setPref] = useState<{ revision: number; data: Preferences } | null>(null)
  const [originalPref, setOriginalPref] = useState(''),
    [options, setOptions] = useState<LLMOptionsResponse>({ options: [], active: null })
  const [model, setModel] = useState<LLMSelection | null>(null),
    [sources, setSources] = useState<Source[]>([]),
    [sourceIds, setSourceIds] = useState<string[]>([])
  const [snapshot, setSnapshot] = useState<{
    path: string
    hash: string
    selection: TextSelection
  } | null>(null)
  const op = useOperation()
  async function show() {
    if (!file) return
    const selected = { ...selection },
      path = file.path
    await flush()
    const [saved, settings, models, ...evidence] = await Promise.all([
      studio<{ path: string; hash: string; text: string }>(
        workspace,
        `/manuscripts/${manuscript}/file?path=${encodeURIComponent(path)}`,
      ),
      studio<{ revision: number; data: Preferences }>(workspace, '/writing/preferences'),
      listLLMOptions(),
      ...['evidence', 'observation', 'claim', 'artifact'].map((kind) =>
        records<Source['data']>(workspace, kind),
      ),
    ])
    if (Array.from(saved.text).slice(selected.start, selected.end).join('') !== selected.text)
      throw new Error(t('选区已变化，请重新选择原文。'))
    setSnapshot({ path, hash: saved.hash, selection: selected })
    const preceding = Array.from(saved.text).slice(0, selected.start).join('')
    const heading = [...preceding.matchAll(/\\(?:begin\{(abstract)\}|(?:sub)*section\*?\{([^}]+)\})|^#{1,4}\s+(.+)$/gmi)].at(-1)
    const currentHeading = heading?.slice(1).find(Boolean) || ''
    const guides = [['abstract', /abstract|摘要/i], ['introduction', /introduction|引言|绪论/i],
      ['related-work', /related work|相关工作/i], ['method', /method|approach|方法/i],
      ['experiments', /experiment|evaluation|result|实验|结果/i], ['conclusion', /conclusion|结论/i]] as const
    setSection(guides.find(([, pattern]) => pattern.test(currentHeading))?.[0] || 'paragraph-flow')
    setInstruction('')
    setPref(settings)
    setOriginalPref(JSON.stringify(settings.data))
    setOptions(models)
    setSources(evidence.flat())
    setSourceIds([])
    setOpen(true)
  }
  async function generate() {
    if (!snapshot || !pref) return
    if (JSON.stringify(pref.data) !== originalPref) {
      const saved = await studio<typeof pref>(workspace, '/writing/preferences', {
        method: 'PUT',
        body: JSON.stringify({ ...pref.data, expected_revision: pref.revision }),
      })
      setPref(saved)
      setOriginalPref(JSON.stringify(saved.data))
    }
    const row = await post<StudioRecord<ProposalData>>(
      workspace,
      `/manuscripts/${manuscript}/assist`,
      {
        path: snapshot.path,
        base_hash: snapshot.hash,
        start: snapshot.selection.start,
        end: snapshot.selection.end,
        mode,
        instruction,
        source_ids: sourceIds,
        llm_selection: model,
        section: section || null,
        language: i18n.language.startsWith('zh') ? 'zh' : 'en',
      },
    )
    await proposed(row)
    setOpen(false)
  }
  return (
    <>
      <Action
        secondary
        disabled={op.busy || !file || !/\.(tex|md)$/i.test(file.path)}
        onClick={() => op.run(show)}
      >
        <Sparkles size={13} /> {t('论文写作助手')}
      </Action>
      {!open && <ErrorNotice message={op.error} />}
      <Dialog
        open={open}
        onClose={() => {
          if (!op.busy) setOpen(false)
        }}
        title={t('论文写作助手')}
        size="lg"
        className="studio-dialog"
        footer={
          <Action
            disabled={op.busy || !pref || !options.options.length || (!snapshot?.selection.text && (!section || !instruction.trim()))}
            onClick={() => op.run(generate)}
          >
            {op.busy ? t('正在生成建议…') : t('生成修改建议')}
          </Action>
        }
      >
        <p className="studio-note">
          {t(
            '发送范围：当前选区、你勾选的来源摘要、稿件引用键及下方写作偏好。生成后会展示逐项差异与事实变化。',
          )}
        </p>
        {snapshot?.selection.text ? <pre className="studio-diff">{snapshot.selection.text}</pre> :
          <p className="studio-note">{t('草稿会插入当前光标处。请写下本段想说明的问题、方法或已有结果。')}</p>}
        <Field label={t('章节指南')}>
          <select value={section} onChange={(event) => setSection(event.target.value)}>
            {[
              ['paragraph-flow', t('段落衔接')], ['abstract', t('摘要')], ['introduction', t('引言')],
              ['related-work', t('相关工作')], ['method', t('方法')], ['experiments', t('实验')],
              ['conclusion', t('结论')], ['figures-tables', t('图表与说明')], ['self-review', t('投稿前自查')],
              ['', t('仅按我的要求修改')],
            ].map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </Field>
        {section && <p className="studio-note">{t('写作指南来自彭思达的公开笔记，由 Master-cai 整理。修改建议附有段落提纲、自查和论据对照。')}</p>}
        <div className="studio-form-grid">
          <Field label={t("修改目的")}>
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              {[
                ['polish', t('学术润色')],
                ['concise', t('压缩篇幅')],
                ['translate_en', t('译为英文')],
                ['translate_zh', t('译为中文')],
                ['abstract', t('整理为摘要草稿')],
                ['caption', t('整理为图注草稿')],
                ['related_work', t('整理为相关工作草稿')],
              ].map(([v, l]) => (
                <option value={v} key={v}>
                  {l}
                </option>
              ))}
            </select>
          </Field>
          <div>
            <span className="studio-note">{t('使用模型')}</span>
            <ModelSelector
              options={options.options}
              activeDefault={options.active}
              value={model}
              loading={false}
              error={false}
              onChange={setModel}
              onRefresh={() =>
                op.run(async () => setOptions(await listLLMOptions({ force: true })))
              }
            />
          </div>
        </div>
        <Field label={t("具体要求")}>
          <textarea
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            placeholder={t('例如：保留‘仅在已见物体上’这一限制，缩短重复表述。')}
            rows={2}
            maxLength={4000}
          />
        </Field>
        <details>
          <summary>
            {t('为这次修改附上证据 · {{count}} 项', { count: sourceIds.length })}</summary>
          <div className="studio-source-picker">
            {sources.map((source) => (
              <label className="studio-check" key={source.id}>
                <input
                  type="checkbox"
                  checked={sourceIds.includes(source.id)}
                  disabled={op.busy || (!sourceIds.includes(source.id) && sourceIds.length >= 20)}
                  onChange={() =>
                    setSourceIds((ids) =>
                      ids.includes(source.id)
                        ? ids.filter((id) => id !== source.id)
                        : [...ids, source.id],
                    )
                  }
                />
                <span>
                  {source.data.title ||
                    source.data.label ||
                    source.data.quote?.slice(0, 100) ||
                    source.id}
                  <small> · {studioLabel(t, source.data.verification || source.data.status || source.kind)}</small>
                </span>
              </label>
            ))}
          </div>
        </details>
        {pref && (
          <label className="studio-check">
            <input type="checkbox" checked={pref.data.allow_cloud} onChange={(event) =>
              setPref({ ...pref, data: { ...pref.data, allow_cloud: event.target.checked } })} />
            {t('允许此项目使用云端写作模型（包括 Codex OAuth）')}
          </label>
        )}
        {pref && (
          <details>
            <summary>{t('此项目的术语、风格与模型范围')}</summary>
            <Field label={t("个人术语表")}>
              <textarea
                rows={3}
                value={pref.data.terminology}
                maxLength={10000}
                onChange={(e) =>
                  setPref({ ...pref, data: { ...pref.data, terminology: e.target.value } })
                }
                placeholder={t('human-object interaction → 人物—物体交互；保留方法缩写。')}
              />
            </Field>
            <Field label={t("风格说明或例句")}>
              <textarea
                rows={3}
                value={pref.data.style_examples}
                maxLength={10000}
                onChange={(e) =>
                  setPref({ ...pref, data: { ...pref.data, style_examples: e.target.value } })
                }
              />
            </Field>
            <p className="studio-note">
              {t('关闭时只接受本机模型，调用失败会保留原文。偏好在点击生成时保存到当前项目。')}
            </p>
          </details>
        )}
        <ErrorNotice message={op.error} />
      </Dialog>
    </>
  )
}
