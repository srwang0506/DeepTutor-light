import assert from 'node:assert/strict'
import test from 'node:test'
import { localRequestAllowed, localCookieName } from '../lib/local-access'
import { prepareBackendForwardHeaders } from '../lib/backend-forward'
import { NextRequest } from 'next/server'
import { proxy } from '../proxy'

test('parallel local instances keep distinct cookie identities', () => {
  assert.notEqual(localCookieName('instance-a'), localCookieName('instance-b'))
  assert.equal(localCookieName('test'), 'deeptutor_local_session_9f86d081884c')
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
    })
    assert.equal(localRequestAllowed(valid), true)
    assert.equal(
      prepareBackendForwardHeaders(valid).get('x-deeptutor-local-capability'),
      'fixture-server-only'
    )
    for (const headers of [
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
