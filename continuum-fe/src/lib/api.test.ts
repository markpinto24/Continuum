import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api, onUnauthorized, setMode, streamChat } from './api'

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

afterEach(() => {
  vi.unstubAllGlobals()
  setMode(null)
})

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

describe('the mode', () => {
  it('names the graph on every request once the app has picked one', async () => {
    const fetchMock = respond(200, { nodes: [], edges: [] })
    await api.graph()
    setMode('personal')
    await api.graph()

    const sent = fetchMock.mock.calls.map(
      (call) => ((call as unknown as [string, RequestInit])[1].headers as Record<string, string>)['x-continuum-mode'],
    )
    expect(sent).toEqual([undefined, 'personal'])
  })

  it('puts it in the query of a download link, which cannot carry a header', () => {
    setMode('personal')
    expect(api.memoryExportUrl()).toMatch(/\/memories\/export\?mode=personal$/)
    expect(api.feedbackExportUrlFor('extraction')).toContain('kind=extraction')
    expect(api.feedbackExportUrlFor('extraction')).toContain('mode=personal')
  })

  it('keeps health out of an export unless the person opts in', () => {
    setMode('personal')
    expect(api.feedbackExportUrl()).not.toContain('include_sensitive')
    expect(api.feedbackExportUrl({ sensitive: true })).toContain('include_sensitive=true')
  })

  it('creates a key for one mode, and sets the default only through its own call', async () => {
    const fetchMock = respond(201, { key: {}, secret: 'ck_x' })
    await api.createApiKey('life agent', 'personal')
    await api.setDefaultMode('personal')

    const [[, create], [url, patch]] = fetchMock.mock.calls as unknown as [string, RequestInit][]
    expect(JSON.parse(create.body as string)).toEqual({ name: 'life agent', mode: 'personal' })
    expect(url).toContain('/auth/mode')
    expect(patch.method).toBe('PATCH')
    expect(JSON.parse(patch.body as string)).toEqual({ default_mode: 'personal' })
  })
})
