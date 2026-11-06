const params = new URLSearchParams(location.search)
let owner = params.get('owner') || 'local',
  workspace = params.get('workspace') || '',
  items = [],
  db,
  queue = Promise.resolve(),
  busy = false
const status = document.querySelector('#status'),
  list = document.querySelector('#items'),
  selector = document.querySelector('#workspace')
function say(message) {
  status.textContent = message
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
  queue.catch(() => say('保存失败，请保持此页打开并复制你的答案。'))
  return queue
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
  b.onclick = () => Promise.resolve(fn()).catch((e) => say(e.message))
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
      o.textContent = id || '默认工作区'
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
        button('打开已保存原文', () => {
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
      answer.setAttribute('aria-label', '你的答案')
      answer.value = item.answer || ''
      answer.disabled = item.state !== 'draft'
      answer.oninput = () => {
        item.answer = answer.value
        void save({ ...item })
      }
      card.append(options, answer)
      const feedback = document.createElement('p')
      feedback.textContent =
        item.receipt?.feedback ||
        {
          draft: '答案保存在本机，尚未提交',
          queued: '等待联网提交',
          grading: '正在评阅，稍后可再次领取回执',
          committed: '已完成评阅，离线作答的独立性未核实',
          ungraded: '暂未完成评阅，可重试',
          cancelled: '已取消',
        }[item.state] ||
        '等待确认'
      card.append(feedback)
      if (item.state === 'draft')
        actions.append(
          button('保存为待提交答案', async () => {
            if (!item.answer?.trim()) throw new Error('先填写你的答案。')
            item.state = 'queued'
            await save({ ...item })
            render()
            say('答案已排入队列。联网后点击上方提交。')
          }),
        )
      if (item.state === 'ungraded')
        actions.append(
          button('重新请求评阅', async () => {
            item.state = 'queued'
            item.submission.retry = true
            await save({ ...item })
            render()
          }),
        )
    }
    actions.append(
      button(
        '移除离线副本',
        async () => {
          if (!confirm('移除这份离线副本？未提交答案也会被移除。')) return
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
    p.textContent = '还没有离线内容。在原文阅读页或练习题上选择“保存供离线使用”。'
    list.append(p)
  }
}
async function sync() {
  if (busy) return
  busy = true
  document.querySelector('#sync').disabled = true
  try {
    await queue
    const auth = await fetch('/api/auth/status', { credentials: 'include', cache: 'no-store' })
    if (!auth.ok) throw new Error('请回工作台登录后再提交。')
    const account = await auth.json()
    if ((account.enabled && !account.authenticated) || (account.user_id || 'local') !== owner)
      throw new Error('当前登录账号与离线内容不匹配，请使用原账号。')
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
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ ...item.submission, answer: item.answer, offline: true }),
            }),
      })
      const body = await res.json()
      if (!res.ok)
        throw new Error(typeof body.detail === 'string' ? body.detail : `提交失败 (${res.status})`)
      item.receipt = body
      item.state = body.state
      await save({ ...item })
      render()
    }
    say('已同步可提交的答案；评阅中的题目可稍后再次领取回执。')
  } finally {
    busy = false
    document.querySelector('#sync').disabled = false
  }
}
selector.onchange = () => {
  workspace = selector.value
  render()
}
document.querySelector('#sync').onclick = () => sync().catch((e) => say(e.message))
document.querySelector('#clear').onclick = async () => {
  if (!confirm('清除此工作区的所有离线资料和未提交答案？')) return
  await queue
  for (const item of items.filter((i) => i.workspace === workspace)) await remove(item.id)
  await reload()
}
const network = () => {
  document.querySelector('#network').textContent = navigator.onLine ? '设备已联网' : '离线模式'
}
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
} catch (e) {
  say(e.message)
}
