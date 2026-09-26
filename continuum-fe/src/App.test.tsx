import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App'

/**
 * Shell smoke test. The WebGL scene cannot render under jsdom, so
 * `belief-graph` is stubbed — everything around it is real, which is enough to
 * catch the wiring mistakes that a type check does not: a panel that never
 * mounts, a badge that counts the wrong thing, a workspace that renders before
 * anyone has signed in.
 */
vi.mock('@/components/belief-graph', () => ({
  BeliefGraph: ({ data }: { data: { nodes: unknown[] } }) => (
    <div data-testid="graph">{data.nodes.length} nodes</div>
  ),
}))

const graph = {
  nodes: [
    {
      id: 'a',
      label: 'Atlas runs on Postgres',
      category: 'decision' as const,
      status: 'active' as const,
      confidence: 0.8,
      subject: 'atlas',
      created_at: '2026-03-04T00:00:00Z',
    },
    {
      id: 'b',
      label: 'Atlas runs on Mongo',
      category: 'decision' as const,
      status: 'contradicted' as const,
      confidence: 0.7,
      subject: 'atlas',
      created_at: '2026-03-05T00:00:00Z',
    },
  ],
  edges: [{ source: 'a', target: 'b', kind: 'conflicts_with' as const }],
}

const auth = vi.hoisted(() => {
  class ApiError extends Error {
    constructor(
      message: string,
      readonly status: number,
    ) {
      super(message)
    }
  }
  const me = { user_id: 'mark', email: 'mark@example.com', is_admin: true, via: 'session' as const }
  return {
    ApiError,
    me,
    state: { needsSetup: false, signedIn: true },
    listener: null as null | (() => void),
    login: vi.fn(() => Promise.resolve(me)),
    setup: vi.fn(() => Promise.resolve(me)),
  }
})

vi.mock('@/lib/api', () => ({
  ApiError: auth.ApiError,
  onUnauthorized: (listener: () => void) => {
    auth.listener = listener
    return () => {}
  },
  api: {
    health: () =>
      Promise.resolve({
        status: 'ok',
        app: 'Continuum',
        environment: 'test',
        qdrant: 'up',
        llm: 'up',
        database: 'up',
      }),
    graph: () => Promise.resolve(graph),
    conflicts: () => Promise.resolve({ total: 2, conflicts: [] }),
    authStatus: () =>
      Promise.resolve({ needs_setup: auth.state.needsSetup, web_setup_allowed: true }),
    me: () =>
      auth.state.signedIn
        ? Promise.resolve(auth.me)
        : Promise.reject(new auth.ApiError('Not signed in.', 401)),
    login: auth.login,
    setup: auth.setup,
    logout: () => Promise.resolve(),
    speechStatus: () =>
      Promise.resolve({ enabled: true, ready: true, max_seconds: 120, language: 'en' }),
  },
  streamChat: () => Promise.resolve(),
}))

beforeEach(() => {
  auth.state.needsSetup = false
  auth.state.signedIn = true
  auth.login.mockClear()
  auth.setup.mockClear()
  localStorage.clear()
})

describe('the sign-in gate', () => {
  it('offers first-run setup when the instance has no account', async () => {
    auth.state.needsSetup = true
    auth.state.signedIn = false
    render(<App />)

    await waitFor(() => expect(screen.getByText('Create the admin account')).toBeDefined())
    expect(screen.queryByTestId('graph')).toBeNull()
  })

  it('prefills setup with the pre-auth user id, so the old graph is kept', async () => {
    localStorage.setItem('continuum.user_id', 'mark')
    auth.state.needsSetup = true
    auth.state.signedIn = false
    render(<App />)

    await waitFor(() => expect(screen.getByDisplayValue('mark')).toBeDefined())
  })

  it('asks for sign-in and loads nothing until it succeeds', async () => {
    auth.state.signedIn = false
    render(<App />)

    await waitFor(() => expect(screen.getByText('Sign in to Continuum')).toBeDefined())
    expect(screen.queryByTestId('graph')).toBeNull()

    await userEvent.type(screen.getByLabelText('Email'), 'mark@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'correct horse battery')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(auth.login).toHaveBeenCalledWith('mark@example.com', 'correct horse battery')
    await waitFor(() => expect(screen.getByTestId('graph').textContent).toBe('2 nodes'))
  })

  it('returns to sign-in when the session lapses mid-use', async () => {
    render(<App />)
    await waitFor(() => expect(screen.getByTestId('graph')).toBeDefined())

    act(() => auth.listener?.())

    await waitFor(() => expect(screen.getByText('Sign in to Continuum')).toBeDefined())
    expect(screen.queryByTestId('graph')).toBeNull()
  })

  it('shows who is signed in, not an editable user id', async () => {
    render(<App />)

    await waitFor(() => expect(screen.getByText('mark@example.com')).toBeDefined())
    expect(screen.queryByLabelText('User id')).toBeNull()
  })
})

describe('App', () => {
  it('renders the graph and its counts', async () => {
    render(<App />)

    await waitFor(() => expect(screen.getByTestId('graph').textContent).toBe('2 nodes'))
    expect(screen.getByText(/2 beliefs · 1 edges/)).toBeDefined()
  })

  it('badges the inbox with the number of open disputes', async () => {
    // The count has to come from /conflicts, not from counting amber nodes —
    // a dispute whose counterpart is outside the graph window still needs a
    // decision.
    render(<App />)

    await waitFor(() => expect(screen.getByText('2')).toBeDefined())
  })

  it('counts each status in the legend', async () => {
    render(<App />)

    await waitFor(() => expect(screen.getByText('Disputed')).toBeDefined())
    expect(screen.getByText('Active')).toBeDefined()
  })

  it('opens on chat, so the graph has something to talk to', async () => {
    render(<App />)

    await waitFor(() => expect(screen.getByText(/Ask about their work/i)).toBeDefined())
  })
})
