/** Server-only capability handling. Never use NEXT_PUBLIC_ for the secret. */
import type { NextRequest } from 'next/server'
import { NextResponse } from 'next/server'
import { createHash } from 'node:crypto'

export const LOCAL_COOKIE = 'deeptutor_local_session'
export const localCookieName = (capability: string) =>
  LOCAL_COOKIE + '_' + createHash('sha256').update(capability).digest('hex').slice(0, 12)
export function localRequestAllowed(headers: Headers): boolean {
  if (!process.env.DEEPTUTOR_LOCAL_CAPABILITY) return true
  const host = headers.get('host') || ''
  try {
    if (!['localhost', '127.0.0.1', '[::1]'].includes(new URL(`http://${host}`).hostname))
      return false
    const origin = headers.get('origin')
    if (origin && ![`http://${host}`, `https://${host}`].includes(origin)) return false
    if (headers.get('sec-fetch-site') === 'cross-site') return false
    return true
  } catch {
    return false
  }
}
export function localGuard(req: NextRequest): NextResponse | null {
  return localRequestAllowed(req.headers)
    ? null
    : NextResponse.json(
        { detail: 'This request did not come from the local workbench.' },
        { status: 403 }
      )
}
export function withLocalSession(response: NextResponse): NextResponse {
  const capability = process.env.DEEPTUTOR_LOCAL_CAPABILITY
  if (capability)
    response.cookies.set(localCookieName(capability), capability, {
      httpOnly: true,
      sameSite: 'strict',
      path: '/',
      maxAge: 86400,
    })
  return response
}
