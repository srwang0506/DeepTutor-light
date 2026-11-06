import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { chromium } from '@playwright/test'
const root = new URL('../public/', import.meta.url)
const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://localhost')
    if (
      ![
        '/offline/',
        '/offline/index.html',
        '/offline/app.js',
        '/offline/style.css',
        '/offline/sw.js',
      ].includes(url.pathname)
    ) {
      res.writeHead(404).end()
      return
    }
    const file = url.pathname === '/offline/' ? 'offline/index.html' : url.pathname.slice(1)
    res.setHeader(
      'Content-Type',
      file.endsWith('.js')
        ? 'application/javascript'
        : file.endsWith('.css')
          ? 'text/css'
          : 'text/html',
    )
    res.end(await readFile(new URL(file, root)))
  } catch {
    res.writeHead(500).end()
  }
})
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
const origin = `http://127.0.0.1:${server.address().port}`
let browser
try {
  browser = await chromium.launch({
    headless: true,
    ...(process.env.DEEPTUTOR_BROWSER_CHANNEL
      ? { channel: process.env.DEEPTUTOR_BROWSER_CHANNEL }
      : {}),
  })
  // The interface locale must override the browser's own language.
  const context = await browser.newContext({ locale: 'zh-CN' })
  const page = await context.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))
  await page.goto(origin + '/offline/?workspace=course&owner=local&lang=en')
  await page.getByRole('heading', { name: 'Offline study', exact: true }).waitFor()
  assert.equal(await page.locator('html').getAttribute('lang'), 'en')
  await page.getByRole('button', { name: 'Submit queued answers online' }).waitFor()
  await page.goto(origin + '/offline/?workspace=course&owner=local&lang=zh')
  await page.getByRole('heading', { name: '离线学习', exact: true }).waitFor()
  await page.evaluate(async () => {
    await navigator.serviceWorker.register('/offline/sw.js', { scope: '/offline/' })
    await navigator.serviceWorker.ready
    const db = await new Promise((resolve, reject) => {
      const r = indexedDB.open('deeptutor-offline-v1', 1)
      r.onsuccess = () => resolve(r.result)
      r.onerror = () => reject(r.error)
    })
    await new Promise((resolve, reject) => {
      const tx = db.transaction('items', 'readwrite')
      tx.objectStore('items').put({
        id: 'offline-quiz',
        owner: 'local',
        workspace: 'course',
        kind: 'quiz',
        title: 'Which ordering does a queue use?',
        question: { options: { A: 'FIFO', B: 'LIFO' } },
        answer: '',
        state: 'draft',
        submission: {
          session_id: 'session',
          turn_id: 'turn',
          question_id: 'question',
          question_version: 'version',
          attempt_id: 'offline-attempt-stable',
          assistance: 'independent',
          offline: true,
          answer_images: [],
        },
      })
      tx.objectStore('items').put({
        id: 'other-account',
        owner: 'someone-else',
        workspace: 'course',
        kind: 'quiz',
        title: 'PRIVATE OTHER ACCOUNT',
      })
      tx.oncomplete = resolve
      tx.onerror = () => reject(tx.error)
    })
    db.close()
  })
  await page.reload()
  await page.getByRole('heading', { name: 'Which ordering does a queue use?' }).waitFor()
  assert.equal(await page.getByText('PRIVATE OTHER ACCOUNT').count(), 0)
  await context.setOffline(true)
  await page.reload()
  await page.getByLabel('你的答案').fill('A')
  await page.getByRole('button', { name: '保存为待提交答案' }).click()
  await page.getByText('等待联网提交', { exact: true }).waitFor()
  await page.reload()
  assert.equal(await page.getByLabel('你的答案').inputValue(), 'A')
  await context.setOffline(false)
  let submissions = []
  await page.route('**/api/auth/status', (r) => r.fulfill({ json: { enabled: false } }))
  await page.route('**/api/quiz/submit?*', (r) => {
    submissions.push(r.request().postDataJSON())
    assert.equal(new URL(r.request().url()).searchParams.get('dt_workspace'), 'course')
    return r.fulfill({
      json: {
        attempt_id: 'offline-attempt-stable',
        state: 'committed',
        assessment_committed: true,
        result: 'correct',
        independent: false,
        evidence_kind: 'unknown',
        feedback: '离线回执已保存',
      },
    })
  })
  await page.getByRole('button', { name: '联网提交待评阅答案' }).click()
  await page.getByText('离线回执已保存', { exact: true }).waitFor()
  await page.getByRole('button', { name: '联网提交待评阅答案' }).click()
  assert.equal(submissions.length, 1)
  assert.equal(submissions[0].attempt_id, 'offline-attempt-stable')
  assert.equal(submissions[0].offline, true)
  assert.equal(submissions[0].answer, 'A')
  await page.evaluate(() => localStorage.setItem('deeptutor-language', 'en'))
  await page.goto(origin + '/offline/?workspace=course&owner=local')
  await page.getByRole('heading', { name: 'Offline study', exact: true }).waitFor()
  await page.getByLabel('Your answer').waitFor()
  const languagePage = await context.newPage()
  await languagePage.goto(origin + '/offline/?workspace=course&owner=local')
  await languagePage.evaluate(() => localStorage.setItem('deeptutor-language', 'zh'))
  await page.getByRole('heading', { name: '离线学习', exact: true }).waitFor()
  await page.getByLabel('你的答案').waitFor()
  assert.equal(await page.getByLabel('你的答案').inputValue(), 'A')
  await languagePage.close()
  assert.deepEqual(errors, [])
  console.log(
    JSON.stringify({
      passed: true,
      checks: [
        'offline reload',
        'account partition',
        'durable answer',
        'stable attempt identity',
        'scoped replay',
        'committed receipt',
        'no duplicate submission',
        'no browser exceptions',
        'explicit interface language overrides browser locale',
        'saved application language applies to offline study',
        'language changes preserve saved answers',
      ],
    }),
  )
} finally {
  if (browser) await browser.close()
  await new Promise((resolve) => server.close(resolve))
}
