import { apiFetch, apiUrl } from './api'
import { scopedUrl } from './workspace-scope'

export interface StudioRecord<T> {
  id: string
  kind: string
  scope: string
  revision: number
  data: T
  created: number
  updated: number
  stale?: unknown[]
}
export interface WorkData {
  title: string
  authors: string[]
  year: number | null
  kind: string
  tags: string[]
  abstract: string
}
export interface OriginalFile {
  hash: string
  name: string
  pages: number
  size: number
  outline: { level: number; title: string; page: number }[]
}
export interface EditionData {
  bibliographic_sources?: { format: string; record: Record<string, unknown> }[]
  bibliographic_conflicts?: {
    field: string
    retained: unknown
    incoming: unknown
    source: string
  }[]
  citekey_aliases?: string[]
  work_id: string
  label: string
  doi: string
  arxiv: string
  citekey: string
  url: string
  venue: string
  full_text: string
  files: OriginalFile[]
}
export type Edition = StudioRecord<EditionData> & { project_role?: string }
export type Work = StudioRecord<WorkData> & { editions: Edition[] }
export interface EvidenceData {
  edition_id: string
  blob_hash: string
  page: number
  page_label: string
  kind: string
  region: [number, number, number, number] | null
  quote: string
  label: string
  interpretation: string
  stance: string
  verification: string
  resolution: string
  anchor_valid?: boolean
  quote_match?: string
  visual_review?: string
  review_note?: string
  claim_support?: string
  needs_review?: boolean
}
export type Protocol = Record<
  | 'dataset'
  | 'dataset_version'
  | 'split'
  | 'population'
  | 'input'
  | 'supervision'
  | 'alignment'
  | 'sampling'
  | 'metric'
  | 'unit'
  | 'threshold'
  | 'aggregation',
  string
> & {
  direction: 'higher' | 'lower' | 'unknown'
  not_applicable: string[]
  field_states?: Record<string, 'known' | 'unknown' | 'not_applicable' | 'conflict'>
  profile?: string
  extensions?: Record<string, string>
}
export const PROTOCOL_FIELDS: [
  keyof Omit<Protocol, 'direction' | 'not_applicable' | 'profile' | 'extensions' | 'field_states'>,
  string,
][] = [
  ['dataset', '数据集'],
  ['dataset_version', '数据版本'],
  ['split', '划分'],
  ['population', '评估对象'],
  ['input', '输入条件'],
  ['supervision', '训练与监督'],
  ['alignment', '对齐方式'],
  ['sampling', '采样方式'],
  ['metric', '指标'],
  ['unit', '单位'],
  ['threshold', '阈值及单位'],
  ['aggregation', '聚合方式'],
]
export const emptyProtocol = (): Protocol => ({
  profile: 'general',
  extensions: {},
  dataset: '',
  dataset_version: '',
  split: '',
  population: '',
  input: '',
  supervision: '',
  alignment: '',
  sampling: '',
  metric: '',
  unit: '',
  threshold: '',
  aggregation: '',
  direction: 'unknown',
  not_applicable: [],
})
export interface ObservationData {
  source_ref?: { id: string; revision: number }
  title: string
  origin: string
  source_id: string
  protocol: Protocol
  value: number | null
  state: string
  note: string
}
export interface RunData {
  title: string
  status: string
  repository: string
  commit: string
  command: string
  dataset: string
  split: string
  missing: string[]
  log: string
  configuration: Record<string, unknown>
}
export interface ArtifactData {
  title: string
  type: string
  files: Record<string, string>
  comparable?: boolean
  ranking_applied?: boolean
}
export interface ManuscriptData {
  title: string
  main_file: string
  engine: string
  status: string
}
export interface BuildData {
  purpose?: string
  manuscript_id: string
  status: string
  input_files: Record<string, string>
  outputs?: Record<string, string>
  started: number
}
export interface ProposalData {
  manuscript_id: string
  path: string
  before: string
  text: string
  diff: string
  status: string
  fact_changes: { kind: string; before: string[]; after: string[] }[]
}
export interface SubmissionData {
  manuscript_id: string
  build_id: string
  files: Record<string, string>
  created: number
}
export interface DiscoveryItem {
  id: string
  title: string
  authors: string[]
  year: number | null
  doi?: string
  arxiv?: string
  url: string
  references: string[]
}
export interface DiscoveryData {
  query: string
  status: string
  order?: string
  providers?: string[]
  year_from?: number | null
  year_to?: number | null
  pages: {
    provider: string
    status: string
    total?: number
    items: DiscoveryItem[]
    error?: string
    retry_after?: string
    next_cursor?: string | null
    started?: number
    query?: Record<string, unknown>
    coverage?: { mode: string; maximum: number; limited?: boolean; snapshot_stable: boolean }
  }[]
}

export function studioUrl(path: string, workspace: string) {
  return apiUrl(scopedUrl('/api/studio' + path, workspace))
}
export class StudioRequestError extends Error {
  constructor(message: string, public status: number) {
    super(message)
  }
}
export async function studio<T>(
  workspace: string,
  path: string,
  init: RequestInit = {},
  signal?: AbortSignal
): Promise<T> {
  const headers = new Headers(init.headers)
  if (typeof init.body === 'string' && !headers.has('Content-Type'))
    headers.set('Content-Type', 'application/json')
  const response = await apiFetch(studioUrl(path, workspace), {
    cache: 'no-store',
    ...init,
    headers,
    signal: signal || init.signal || AbortSignal.timeout(210000),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new StudioRequestError(
      typeof body.detail === 'string'
        ? body.detail
        : 'Request failed. Check your input and try again.',
      response.status,
    )
  }
  return response.json() as Promise<T>
}
export const post = <T>(workspace: string, path: string, data: unknown) =>
  studio<T>(workspace, path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
export const records = <T>(workspace: string, kind: string, signal?: AbortSignal) =>
  studio<{ records: StudioRecord<T>[] }>(workspace, `/records/${kind}`, {}, signal).then(
    r => r.records
  )
