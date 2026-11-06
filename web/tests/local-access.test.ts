import assert from 'node:assert/strict'
import test from 'node:test'
import {
  localRequestAllowed,
  localCookieName,
  localSessionToken,
  localBootstrapToken,
} from '../lib/local-access'
import { prepareBackendForwardHeaders } from '../lib/backend-forward'
import { NextRequest } from 'next/server'
import { proxy } from '../proxy'

test('parallel local instances keep distinct cookie identities', () => {
  assert.notEqual(localCookieName('instance-a'), localCookieName('instance-b'))
  assert.equal(localCookieName('test'), 'deeptutor_local_session_9f86d081884c')
})

test('only a launch link creates a browser session and the redirect removes its secret', () => {
  const before = process.env.DEEPTUTOR_LOCAL_CAPABILITY
  const cap = 'launch-fixture-only'
  process.env.DEEPTUTOR_LOCAL_CAPABILITY = cap
  const request = (url: string, method = 'GET') =>
    new NextRequest(url, { method, headers: { host: '127.0.0.1:3782' } })
  try {
    const anonymous = proxy(request('http://127.0.0.1:3782/today'))
    assert.equal(anonymous.status, 200)
    assert.equal(anonymous.headers.get('set-cookie'), null)
    assert.equal(proxy(request('http://127.0.0.1:3782/api/studio/library')).status, 403)
    assert.equal(proxy(request('http://127.0.0.1:3782/today?dt_bootstrap=invalid')).status, 403)
    const url =
      'http://127.0.0.1:3782/today?dt_workspace=project&dt_bootstrap=' + localBootstrapToken(cap)
    const response = proxy(request(url))
    assert.equal(response.status, 307)
    assert.equal(
      response.headers.get('location'),
      'http://127.0.0.1:3782/today?dt_workspace=project'
    )
    const cookie = response.headers.get('set-cookie') || ''
    assert.ok(cookie.includes(localSessionToken(cap)))
    assert.ok(cookie.includes('HttpOnly') && cookie.includes('SameSite=strict'))
    assert.ok(!cookie.includes(cap) && !cookie.includes(localBootstrapToken(cap)))
    assert.equal(response.headers.get('referrer-policy'), 'no-referrer')
    assert.equal(proxy(request(url, 'POST')).status, 403)
    assert.equal(proxy(request('http://127.0.0.1:3782/auth/callback', 'POST')).status, 405)
  } finally {
    if (before) process.env.DEEPTUTOR_LOCAL_CAPABILITY = before
    else delete process.env.DEEPTUTOR_LOCAL_CAPABILITY
  }
})

test('image optimization can read bundled logos but cannot bypass protected file routes', () => {
  const before = process.env.DEEPTUTOR_LOCAL_CAPABILITY
  process.env.DEEPTUTOR_LOCAL_CAPABILITY = 'fixture-server-only'
  try {
    assert.equal(proxy(new NextRequest('http://localhost/brand/color-logo.png')).status, 200)
    for (const path of ['/api/studio/secret.png', '/files/private.svg', '/chat'])
      assert.equal(proxy(new NextRequest('http://localhost' + path)).status, 403)
  } finally {
    if (before) process.env.DEEPTUTOR_LOCAL_CAPABILITY = before
    else delete process.env.DEEPTUTOR_LOCAL_CAPABILITY
  }
})

test('local UI forwards only its own capability after validating origin and host', () => {
  const before = process.env.DEEPTUTOR_LOCAL_CAPABILITY
  process.env.DEEPTUTOR_LOCAL_CAPABILITY = 'fixture-server-only'
  try {
    const valid = new Headers({
      host: '127.0.0.1:3782',
      origin: 'http://127.0.0.1:3782',
      'x-deeptutor-local-capability': 'injected',
      cookie:
        localCookieName('fixture-server-only') + '=' + localSessionToken('fixture-server-only'),
    })
    assert.equal(localRequestAllowed(valid), true)
    assert.equal(
      prepareBackendForwardHeaders(valid).get('x-deeptutor-local-capability'),
      'fixture-server-only'
    )
    for (const headers of [
      new Headers({ host: '127.0.0.1:3782' }),
      new Headers({ host: '127.0.0.1:3782', origin: 'http://127.0.0.1:3782' }),
      new Headers({ host: 'attacker.example' }),
      new Headers({ host: '127.0.0.1:3782', origin: 'https://attacker.example' }),
      new Headers({ host: '127.0.0.1:3782', 'sec-fetch-site': 'cross-site' }),
    ]) {
      assert.equal(localRequestAllowed(headers), false)
      assert.throws(() => prepareBackendForwardHeaders(headers))
    }
  } finally {
    if (before) process.env.DEEPTUTOR_LOCAL_CAPABILITY = before
    else delete process.env.DEEPTUTOR_LOCAL_CAPABILITY
  }
})
