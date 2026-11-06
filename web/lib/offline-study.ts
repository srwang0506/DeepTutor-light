import { apiFetch } from './api'
import { scopedUrl } from './workspace-scope'
import { randomUuid } from './random-uuid'
export interface OfflineItem {
  id: string
  owner: string
  workspace: string
  kind: 'pdf' | 'quiz'
  title: string
  created: number
  blob?: Blob
  question?: unknown
  submission?: Record<string, unknown>
  answer?: string
  state?: string
  receipt?: unknown
}
export async function offlineOwner(): Promise<string> {
  const r = await apiFetch('/api/auth/status')
  if (!r.ok) throw new Error('请先登录后保存离线资料。')
  const a = await r.json()
  if (a.enabled && !a.authenticated) throw new Error('请先登录。')
  return a.user_id || 'local'
}
async function prepare() {
  if (!('serviceWorker' in navigator)) throw new Error('此浏览器不支持离线工作台。')
  const registration = await navigator.serviceWorker.register('/offline/sw.js', {
    scope: '/offline/',
  })
  const worker = registration.installing || registration.waiting || registration.active
  if (worker && worker.state !== 'activated')
    await new Promise<void>((resolve, reject) => {
      worker.addEventListener('statechange', () => {
        if (worker.state === 'activated') resolve()
        if (worker.state === 'redundant') reject(new Error('离线入口安装失败。'))
      })
    })
}
export async function saveOffline(item: OfflineItem) {
  await prepare()
  if (item.blob && item.blob.size > 50_000_000) throw new Error('单份离线资料不能超过 50 MB。')
  const db = await new Promise<IDBDatabase>((resolve, reject) => {
    const r = indexedDB.open('deeptutor-offline-v1', 1)
    r.onupgradeneeded = () => r.result.createObjectStore('items', { keyPath: 'id' })
    r.onsuccess = () => resolve(r.result)
    r.onerror = () => reject(r.error)
  })
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction('items', 'readwrite')
      tx.objectStore('items').put(item)
      tx.oncomplete = () => resolve()
      tx.onabort = () => reject(tx.error)
      tx.onerror = () => reject(tx.error)
    })
  } finally {
    db.close()
  }
}
export async function saveQuizOffline(
  workspace: string,
  identity: { session_id: string; turn_id: string; question_id: string; question_version: string },
  question: { question: string; options?: unknown; question_type?: string },
) {
  const owner = await offlineOwner()
  await saveOffline({
    id: randomUuid(),
    owner,
    workspace,
    kind: 'quiz',
    title: question.question,
    created: Date.now(),
    question,
    answer: '',
    state: 'draft',
    submission: {
      ...identity,
      attempt_id: randomUuid(),
      answer_images: [],
      assistance: 'independent',
      offline: true,
    },
  })
  return offlineHref(workspace, owner)
}
export async function savePdfOffline(workspace: string, title: string, url: string) {
  const owner = await offlineOwner()
  const r = await apiFetch(url)
  if (!r.ok) throw new Error('无法保存原文。')
  const blob = await r.blob()
  if (!blob.type.includes('pdf')) throw new Error('这份原文不是 PDF。')
  await saveOffline({
    id: randomUuid(),
    owner,
    workspace,
    kind: 'pdf',
    title,
    created: Date.now(),
    blob,
  })
  return offlineHref(workspace, owner)
}
export const offlineHref = (workspace: string, owner: string) =>
  `/offline/?workspace=${encodeURIComponent(workspace)}&owner=${encodeURIComponent(owner)}`
export const offlineSubmitUrl = (workspace: string) => scopedUrl('/api/quiz/submit', workspace)
