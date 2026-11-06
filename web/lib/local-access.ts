/** Server-only capability handling. Never use NEXT_PUBLIC_ for the secret. */
import type { NextRequest } from 'next/server'
import { NextResponse } from 'next/server'
import { createHash, createHmac, timingSafeEqual } from 'node:crypto'

export const LOCAL_COOKIE = 'deeptutor_local_session'
export const localCookieName = (capability: string) =>
  LOCAL_COOKIE + '_' + createHash('sha256').update(capability).digest('hex').slice(0, 12)
export const localSessionToken = (capability: string) =>
  createHmac('sha256', capability).update('browser-session-v1').digest('hex')
export const localBootstrapToken = (capability: string) =>
  createHmac('sha256', capability).update('browser-bootstrap-v1').digest('hex')
function equalToken(a: string, b: string): boolean {
  return (
    Buffer.byteLength(a) === Buffer.byteLength(b) && timingSafeEqual(Buffer.from(a), Buffer.from(b))
  )
}
export function localRequestAllowed(headers: Headers, sessionRequired = true): boolean {
  if (!process.env.DEEPTUTOR_LOCAL_CAPABILITY) return true
  const host = headers.get('host') || ''
  try {
    if (!['localhost', '127.0.0.1', '[::1]'].includes(new URL(`http://${host}`).hostname))
      return false
    const origin = headers.get('origin')
    if (origin && ![`http://${host}`, `https://${host}`].includes(origin)) return false
    if (headers.get('sec-fetch-site') === 'cross-site') return false
    if (!sessionRequired) return true
    const capability = process.env.DEEPTUTOR_LOCAL_CAPABILITY!
    const name = localCookieName(capability)
    const cookie = (headers.get('cookie') || '')
      .split(';')
      .map(v => v.trim())
      .find(v => v.startsWith(name + '='))
    return !!cookie && equalToken(cookie.slice(name.length + 1), localSessionToken(capability))
  } catch {
    return false
  }
}
export function localGuard(req: NextRequest, sessionRequired = true): NextResponse | null {
  return localRequestAllowed(req.headers, sessionRequired)
    ? null
    : NextResponse.json(
        { detail: 'This request did not come from the local workbench.' },
        { status: 403 }
      )
}
export function withLocalSession(response: NextResponse): NextResponse {
  const capability = process.env.DEEPTUTOR_LOCAL_CAPABILITY
  if (capability)
    response.cookies.set(localCookieName(capability), localSessionToken(capability), {
      httpOnly: true,
      sameSite: 'strict',
      path: '/',
      maxAge: 86400,
    })
  return response
}

export function bootstrapLocalSession(req: NextRequest): NextResponse | null {
  const capability = process.env.DEEPTUTOR_LOCAL_CAPABILITY
  const token = req.nextUrl.searchParams.get('dt_bootstrap')
  if (!capability || !token) return null
  if (
    req.method !== 'GET' ||
    !localRequestAllowed(req.headers, false) ||
    !equalToken(token, localBootstrapToken(capability))
  )
    return NextResponse.json({ detail: 'Invalid local launch link.' }, { status: 403 })
  // NextURL normalizes loopback addresses to localhost. Keep the validated
  // browser Host so the HttpOnly cookie and redirect remain on one origin.
  const clean = new URL(req.nextUrl.href)
  clean.host = req.headers.get('host')!
  clean.searchParams.delete('dt_bootstrap')
  const response = withLocalSession(NextResponse.redirect(clean))
  response.headers.set('Cache-Control', 'no-store')
  response.headers.set('Referrer-Policy', 'no-referrer')
  return response
}
