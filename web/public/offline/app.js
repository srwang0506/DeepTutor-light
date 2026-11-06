const params = new URLSearchParams(location.search)
const COPY = {
  en: {
    back: '← Back to workspace',
    title: 'Offline study',
    intro: 'Continue with your saved materials anytime. Assessment records are created only after you submit an answer.',
    workspace: 'Workspace',
    sync: 'Submit queued answers online',
    clear: 'Clear this workspace’s offline content',
    privacy: 'Offline content is stored in this browser, including questions, answers and selected PDFs. Save it only on a trusted personal device. Clearing browser data removes unsubmitted answers. Independence of offline answers cannot be verified.',
    saveFailed: 'Could not save. Keep this page open and copy your answer.',
    defaultWorkspace: 'Default workspace',
    openSource: 'Open saved source',
    answer: 'Your answer',
    draft: 'Saved on this device; not submitted yet',
    queued: 'Waiting for an internet connection to submit',
    grading: 'Assessment in progress. Check again later for the receipt.',
    committed: 'Assessment complete. Independence of the offline answer has not been verified.',
    ungraded: 'Assessment is incomplete. You can retry.',
    cancelled: 'Cancelled',
    pending: 'Waiting for confirmation',
    queueAnswer: 'Queue answer for submission',
    answerRequired: 'Write your answer first.',
    answerQueued: 'Your answer is queued. Use the submit button above when online.',
    retry: 'Request assessment again',
    remove: 'Remove offline copy',
    removeConfirm: 'Remove this offline copy? Unsubmitted answers will also be removed.',
    empty: 'No offline content yet. Choose “Save for offline use” on a source document or practice question.',
    login: 'Return to the workspace and sign in before submitting.',
    accountMismatch: 'The signed-in account does not match this offline content. Use the original account.',
    submissionFailed: 'Submission failed ({status})',
    synced: 'Eligible answers have been synced. Check again later for assessments in progress.',
    clearConfirm: 'Clear all offline materials and unsubmitted answers in this workspace?',
    online: 'Connected',
    offline: 'Offline',
    storageUnavailable: 'Offline storage is unavailable. Keep this page open and copy any unsaved answers.',
    requestFailed: 'The action could not be completed. Please try again.',
  },
  zh: {
    back: '← 返回工作台',
    title: '离线学习',
    intro: '已经保存的资料，随时可以继续。答案提交后才会产生评阅记录。',
    workspace: '工作区',
    sync: '联网提交待评阅答案',
    clear: '清除此工作区的离线内容',
    privacy: '离线内容保存在这个浏览器，包括题目、答案和选中的 PDF。请仅在你信任的个人设备保存；清除浏览器数据会移除未提交答案。离线作答的独立性无法核实。',
    saveFailed: '保存失败，请保持此页打开并复制你的答案。',
    defaultWorkspace: '默认工作区',
    openSource: '打开已保存原文',
    answer: '你的答案',
    draft: '答案保存在本机，尚未提交',
    queued: '等待联网提交',
    grading: '正在评阅，稍后可再次领取回执',
    committed: '已完成评阅，离线作答的独立性未核实',
    ungraded: '暂未完成评阅，可重试',
    cancelled: '已取消',
    pending: '等待确认',
    queueAnswer: '保存为待提交答案',
    answerRequired: '先填写你的答案。',
    answerQueued: '答案已排入队列。联网后点击上方提交。',
    retry: '重新请求评阅',
    remove: '移除离线副本',
    removeConfirm: '移除这份离线副本？未提交答案也会被移除。',
    empty: '还没有离线内容。在原文阅读页或练习题上选择“保存供离线使用”。',
    login: '请回工作台登录后再提交。',
    accountMismatch: '当前登录账号与离线内容不匹配，请使用原账号。',
    submissionFailed: '提交失败（{status}）',
    synced: '已同步可提交的答案；评阅中的题目可稍后再次领取回执。',
    clearConfirm: '清除此工作区的所有离线资料和未提交答案？',
    online: '设备已联网',
    offline: '离线模式',
    storageUnavailable: '离线存储暂时不可用，请保持此页打开并复制未保存的答案。',
    requestFailed: '暂时无法完成此操作，请稍后重试。',
  },
}
function storedLanguage() {
  try {
    return localStorage.getItem('deeptutor-language')
  } catch {
    return null
  }
}
function normalizeLanguage(value) {
  return /^(zh|cn|chinese)(?:[-_]|$)/i.test(value || '') ? 'zh' : 'en'
}
let language = normalizeLanguage(params.get('lang') || storedLanguage())
const text = (key, values = {}) =>
  (COPY[language][key] || COPY.en[key] || key).replace(/\{(\w+)\}/g, (_, name) => String(values[name] ?? ''))
let statusMessage = null
function applyLanguage() {
  document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en'
  document.title = `${text('title')} · DeepTutor`
  for (const element of document.querySelectorAll('[data-copy]'))
    element.textContent = text(element.dataset.copy)
  if (statusMessage) say(...statusMessage)
}
let owner = params.get('owner') || 'local',
  workspace = params.get('workspace') || '',
  items = [],
  db,
  queue = Promise.resolve(),
  busy = false
const status = document.querySelector('#status'),
  list = document.querySelector('#items'),
  selector = document.querySelector('#workspace')
function say(key, values = {}) {
  statusMessage = [key, values]
  status.textContent = text(key, values)
}
function uiError(key, values = {}) {
  const error = new Error(key)
  error.copyKey = key
  error.copyValues = values
  return error
}
function showError(error) {
  say(error?.copyKey || 'requestFailed', error?.copyValues)
}
function save(item) {
  queue = queue
    .catch(() => {})
    .then(
      () =>
        new Promise((resolve, reject) => {
          const tx = db.transaction('items', 'readwrite')
          tx.objectStore('items').put(item)
          tx.oncomplete = resolve
          tx.onerror = () => reject(tx.error)
        }),
    )
  queue.catch(() => say('saveFailed'))
  return queue.catch(() => { throw uiError('saveFailed') })
}
function remove(id) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction('items', 'readwrite')
    tx.objectStore('items').delete(id)
    tx.oncomplete = resolve
    tx.onerror = () => reject(tx.error)
  })
}
function button(text, fn, secondary = false) {
  const b = document.createElement('button')
  b.textContent = text
  b.type = 'button'
  if (secondary) b.className = 'secondary'
  b.onclick = () => Promise.resolve(fn()).catch(showError)
  return b
}
function scoped(path) {
  const url = new URL(path, location.origin)
  url.searchParams.set('dt_workspace', workspace)
  return url
}
async function reload() {
  await queue
  items = await new Promise((resolve, reject) => {
    const r = db.transaction('items').objectStore('items').getAll()
    r.onsuccess = () => resolve(r.result.filter((i) => i.owner === owner))
    r.onerror = () => reject(r.error)
  })
  const scopes = [...new Set([workspace, ...items.map((i) => i.workspace)])]
  selector.replaceChildren(
    ...scopes.map((id) => {
      const o = document.createElement('option')
      o.value = id
      o.textContent = id || text('defaultWorkspace')
      o.selected = id === workspace
      return o
    }),
  )
  render()
}
function render() {
  for (const frame of list.querySelectorAll('iframe')) URL.revokeObjectURL(frame.src)
  list.replaceChildren()
  for (const item of items.filter((i) => i.workspace === workspace)) {
    const card = document.createElement('article'),
      title = document.createElement('h2'),
      actions = document.createElement('div')
    title.textContent = item.title
    actions.className = 'actions'
    card.append(title)
    if (item.kind === 'pdf') {
      actions.append(
        button(text('openSource'), () => {
          if (card.querySelector('iframe')) return
          const frame = document.createElement('iframe')
          frame.title = item.title
          frame.src = URL.createObjectURL(item.blob)
          card.append(frame)
        }),
      )
    } else {
      const options = document.createElement('pre')
      options.textContent = item.question?.options
        ? JSON.stringify(item.question.options, null, 2)
        : ''
      const answer = document.createElement('textarea')
      answer.setAttribute('aria-label', text('answer'))
      answer.value = item.answer || ''
      answer.disabled = item.state !== 'draft'
      answer.oninput = () => {
        item.answer = answer.value
        void save({ ...item }).catch(showError)
      }
      card.append(options, answer)
      const feedback = document.createElement('p')
      feedback.textContent =
        item.receipt?.feedback ||
        text(['draft', 'queued', 'grading', 'committed', 'ungraded', 'cancelled'].includes(item.state) ? item.state : 'pending')
      card.append(feedback)
      if (item.state === 'draft')
        actions.append(
          button(text('queueAnswer'), async () => {
            if (!item.answer?.trim()) throw uiError('answerRequired')
            item.state = 'queued'
            await save({ ...item })
            render()
            say('answerQueued')
          }),
        )
      if (item.state === 'ungraded')
        actions.append(
          button(text('retry'), async () => {
            item.state = 'queued'
            item.submission.retry = true
            await save({ ...item })
            render()
          }),
        )
    }
    actions.append(
      button(
        text('remove'),
        async () => {
          if (!confirm(text('removeConfirm'))) return
          await remove(item.id)
          await reload()
        },
        true,
      ),
    )
    card.append(actions)
    list.append(card)
  }
  if (!list.children.length) {
    const p = document.createElement('p')
    p.textContent = text('empty')
    list.append(p)
  }
}
async function sync() {
  if (busy) return
  busy = true
  document.querySelector('#sync').disabled = true
  try {
    await queue
    const auth = await fetch('/api/auth/status', { credentials: 'include', cache: 'no-store', headers: { 'Accept-Language': language } })
    if (!auth.ok) throw uiError('login')
    const account = await auth.json()
    if ((account.enabled && !account.authenticated) || (account.user_id || 'local') !== owner)
      throw uiError('accountMismatch')
    for (const item of items.filter(
      (i) => i.workspace === workspace && ['queued', 'grading'].includes(i.state),
    )) {
      const checking = item.state === 'grading'
      const url = scoped(
        checking
          ? `/api/quiz/attempts/${encodeURIComponent(item.submission.session_id)}/${encodeURIComponent(item.submission.attempt_id)}`
          : '/api/quiz/submit',
      )
      const res = await fetch(url, {
        credentials: 'include',
        cache: 'no-store',
        ...(checking
          ? {}
          : {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', 'Accept-Language': language },
              body: JSON.stringify({ ...item.submission, answer: item.answer, offline: true }),
            }),
      })
      const body = await res.json()
      if (!res.ok)
        throw uiError('submissionFailed', { status: res.status })
      item.receipt = body
      item.state = body.state
      await save({ ...item })
      render()
    }
    say('synced')
  } finally {
    busy = false
    document.querySelector('#sync').disabled = false
  }
}
selector.onchange = () => {
  workspace = selector.value
  render()
}
document.querySelector('#sync').onclick = () => sync().catch(showError)
async function clearWorkspace() {
  if (!confirm(text('clearConfirm'))) return
  await queue
  for (const item of items.filter((i) => i.workspace === workspace)) await remove(item.id)
  await reload()
}
document.querySelector('#clear').onclick = () => clearWorkspace().catch(showError)
const network = () => {
  document.querySelector('#network').textContent = text(navigator.onLine ? 'online' : 'offline')
}
applyLanguage()
addEventListener('storage', (event) => {
  if (event.key !== 'deeptutor-language' || params.has('lang')) return
  language = normalizeLanguage(event.newValue)
  applyLanguage()
  network()
  if (db) void reload().catch(() => say('storageUnavailable'))
})
addEventListener('online', network)
addEventListener('offline', network)
network()
try {
  db = await new Promise((resolve, reject) => {
    const r = indexedDB.open('deeptutor-offline-v1', 1)
    r.onupgradeneeded = () => r.result.createObjectStore('items', { keyPath: 'id' })
    r.onsuccess = () => resolve(r.result)
    r.onerror = () => reject(r.error)
  })
  await reload()
} catch {
  say('storageUnavailable')
}
