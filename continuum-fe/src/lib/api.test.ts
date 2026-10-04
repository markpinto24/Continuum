import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api, onUnauthorized, streamChat } from './api'

function respond(status: number, body: unknown = {}) {
  const fetchMock = vi.fn(() =>
    Promise.resolve(
      new Response(status === 204 ? null : JSON.stringify(body), {
        status,
        headers: { 'content-type': 'application/json' },
      }),
    ),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => vi.unstubAllGlobals())

describe('api client', () => {
  it('sends the browser header and the cookie on every request', async () => {
    const fetchMock = respond(200, { nodes: [], edges: [] })
    await api.graph()

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).not.toContain('user_id') // identity comes from the session, never the URL
    expect(init.credentials).toBe('same-origin')
    expect((init.headers as Record<string, string>)['x-continuum-client']).toBe('web')
  })

  it('never puts a user_id in a body', async () => {
    const fetchMock = respond(201, { source_id: 's', extracted: 0 })
    await api.ingest('a note')

    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(JSON.parse(init.body as string)).toEqual({ text: 'a note' })
  })

  it('treats a 401 on a data call as a lapsed session', async () => {
    respond(401, { detail: 'Your session has expired. Sign in again.' })
    const lapsed = vi.fn()
    const stop = onUnauthorized(lapsed)

    await expect(api.conflicts()).rejects.toBeInstanceOf(ApiError)
    expect(lapsed).toHaveBeenCalledOnce()
    stop()
  })

  it('treats a 401 from sign-in as a wrong password, not a lapsed session', async () => {
    respond(401, { detail: 'Wrong email or password.' })
    const lapsed = vi.fn()
    const stop = onUnauthorized(lapsed)

    await expect(api.login('a@b.co', 'nope')).rejects.toThrow('Wrong email or password.')
    expect(lapsed).not.toHaveBeenCalled()
    stop()
  })

  it('handles a 204 without trying to parse a body', async () => {
    respond(204)
    await expect(api.logout()).resolves.toBeUndefined()
  })

  it('reports a lapsed session from the chat stream too', async () => {
    respond(401, { detail: 'Not signed in.' })
    const lapsed = vi.fn()
    const stop = onUnauthorized(lapsed)

    await expect(streamChat({ messages: [{ role: 'user', content: 'hi' }] }, {})).rejects.toThrow()
    expect(lapsed).toHaveBeenCalledOnce()
    stop()
  })
})
