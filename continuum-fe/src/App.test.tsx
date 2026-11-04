import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App'
import type { Mode } from './lib/types'

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
  const me = {
    user_id: 'mark',
    email: 'mark@example.com',
    is_admin: true,
    via: 'session' as const,
    mode: 'workspace' as Mode,
    default_mode: 'workspace' as Mode,
  }
  return {
    ApiError,
    me,
    state: { needsSetup: false, signedIn: true },
    listener: null as null | (() => void),
    /** Every mode the app named for its requests, in order. */
    modes: [] as (string | null)[],
    login: vi.fn(() => Promise.resolve(me)),
    setup: vi.fn(() => Promise.resolve(me)),
  }
})

vi.mock('@/lib/api', () => ({
  ApiError: auth.ApiError,
  setMode: (mode: string | null) => {
    auth.modes.push(mode)
  },
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
    voiceSettings: () =>
      Promise.resolve({ voice: 'en_US-lessac-medium', speed: 1, default_voice: 'en_US-lessac-medium', voices: [] }),
    // What the inbox and memory tabs ask for. Left loading: these tests only
    // check which tab opens; the panels have tests of their own.
    rules: () => new Promise(() => {}),
    feedbackActivity: () => new Promise(() => {}),
    feedbackEvidence: () => new Promise(() => {}),
    feedbackSummary: () => new Promise(() => {}),
    calibration: () => new Promise(() => {}),
    pendingMemories: () => new Promise(() => {}),
    memory: () => new Promise(() => {}),
    feedbackExportUrl: () => '#',
    feedbackExportUrlFor: () => '#',
    speechStatus: () =>
      Promise.resolve({
        enabled: true, ready: true, max_seconds: 120, language: 'en',
        synthesis: true, synthesis_ready: true, synthesis_max_chars: 800,
      }),
  },
  streamChat: () => Promise.resolve(),
}))

beforeEach(() => {
  auth.state.needsSetup = false
  auth.state.signedIn = true
  auth.login.mockClear()
  auth.setup.mockClear()
  auth.modes.length = 0
  localStorage.clear()
})

/** Lumen's stage — where every session opens. */
const lumen = () => screen.queryByRole('region', { name: 'Lumen' })
const chatColumn = () => screen.queryByRole('region', { name: 'Chat' })
const memoriesColumn = () => screen.queryByRole('region', { name: 'Memories' })

/** Through the dock, as a person would: each of the three is its own toggle. */
async function toggle(place: 'Memories' | 'Chat' | 'Lumen') {
  await userEvent.click(await screen.findByRole('button', { name: new RegExp(`^${place} — (show|hide)$`) }))
}

/** a comes before b in the page — left of it on screen. */
function before(a: Element | null, b: Element | null): boolean {
  return !!a && !!b && !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
}

describe('the sign-in gate', () => {
  it('offers first-run setup when the instance has no account', async () => {
    auth.state.needsSetup = true
    auth.state.signedIn = false
    render(<App />)

    await waitFor(() => expect(screen.getByText('Create the admin account')).toBeDefined())
    expect(lumen()).toBeNull()
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
    expect(lumen()).toBeNull()

    await userEvent.type(screen.getByLabelText('Email'), 'mark@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'correct horse battery')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(auth.login).toHaveBeenCalledWith('mark@example.com', 'correct horse battery')
    await waitFor(() => expect(lumen()).not.toBeNull())
  })

  it('returns to sign-in when the session lapses mid-use', async () => {
    render(<App />)
    await waitFor(() => expect(lumen()).not.toBeNull())

    act(() => auth.listener?.())

    await waitFor(() => expect(screen.getByText('Sign in to Continuum')).toBeDefined())
    expect(lumen()).toBeNull()
  })

  it('shows who is signed in, not an editable user id', async () => {
    render(<App />)

    await waitFor(() => expect(screen.getByText('mark@example.com')).toBeDefined())
    expect(screen.queryByLabelText('User id')).toBeNull()
  })
})

describe('opening on Lumen', () => {
  it('opens on Lumen alone, waiting for a tap, with a greeting that mentions open disputes', async () => {
    render(<App />)

    await waitFor(() => expect(lumen()).not.toBeNull())
    // The mocked inbox holds 2 conflicts; the user id 'mark' reads as a name.
    await waitFor(() =>
      expect(lumen()?.textContent).toMatch(/Mark\..*2 disagreements are waiting for your decision/),
    )
    // Browsers let the microphone and the voice start only from a click.
    expect(screen.getByRole('button', { name: 'Talk to Lumen' })).toBeDefined()
    expect(screen.queryByTestId('graph')).toBeNull()
    expect(chatColumn()).toBeNull() // there, holding the conversation, but not on screen
  })

  it('badges the inbox in the dock with the open disputes', async () => {
    // The count has to come from /conflicts, not from counting amber nodes —
    // a dispute whose counterpart is outside the graph window still needs a
    // decision.
    render(<App />)

    expect(await screen.findByRole('button', { name: 'Inbox — 2 open' })).toBeDefined()
  })

  it('wears the colours of the memory it talks from', async () => {
    auth.me.default_mode = 'personal'
    try {
      render(<App />)
      await waitFor(() => expect(lumen()).not.toBeNull())
      // Rose in Personal (lib/lumen-theme), not Work's cyan.
      expect(lumen()?.getAttribute('style')).toContain('--hud: #fda4af')
    } finally {
      auth.me.default_mode = 'workspace'
    }
  })
})

describe('memories, Lumen and chat, each on its own', () => {
  it('puts the graph beside Lumen, on its left, with its counts', async () => {
    render(<App />)
    await toggle('Memories')

    await waitFor(() => expect(screen.getByTestId('graph').textContent).toBe('2 nodes'))
    expect(lumen()).not.toBeNull()
    expect(before(memoriesColumn(), lumen())).toBe(true)
    expect(screen.getByText(/2 beliefs · 1 edges/)).toBeDefined()
    expect(screen.getByRole('button', { name: /^Disputed: 1/ })).toBeDefined()
  })

  it('with all three open: memories, then Lumen, then the chat', async () => {
    render(<App />)
    await toggle('Chat')
    await toggle('Memories')

    await waitFor(() => expect(chatColumn()).not.toBeNull())
    expect(before(memoriesColumn(), lumen())).toBe(true)
    expect(before(lumen(), chatColumn())).toBe(true)
  })

  it('closes any of them, but never the last one', async () => {
    render(<App />)
    await toggle('Chat')
    await toggle('Lumen')
    await waitFor(() => expect(lumen()).toBeNull())
    expect(chatColumn()).not.toBeNull()

    await toggle('Chat') // the only one open: it stays
    expect(chatColumn()).not.toBeNull()
  })

  it('Ctrl+\\ brings the chat in and out', async () => {
    render(<App />)
    await waitFor(() => expect(lumen()).not.toBeNull())
    await userEvent.keyboard('{Control>}\\{/Control}')
    expect(chatColumn()).not.toBeNull()
    await userEvent.keyboard('{Control>}\\{/Control}')
    expect(chatColumn()).toBeNull()
  })

  it('the inbox opens as a popup over everything, and closes back to what was there', async () => {
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Inbox — 2 open' }))
    expect(screen.getByRole('dialog', { name: /^Inbox/ }).textContent).toMatch(/2 open/)
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /^Inbox/ })).toBeNull())
    expect(lumen()).not.toBeNull()
  })

  it('a divider between the graph and Lumen resizes Lumen, as the one before the chat does', async () => {
    render(<App />)
    await toggle('Memories')
    await toggle('Chat')
    expect(await screen.findByRole('separator', { name: 'Resize Lumen' })).toBeDefined()
    expect(screen.getByRole('separator', { name: 'Resize the chat' })).toBeDefined()
  })

  it('a memory in the graph opens its detail in its own panel', async () => {
    render(<App />)
    await toggle('Memories')
    await userEvent.click(await screen.findByRole('button', { name: /Atlas runs on Postgres/ }))
    expect(screen.getByRole('dialog', { name: 'Memory' })).toBeDefined()
  })

  it('Lumen takes over from the chat — and says why when it cannot start', async () => {
    render(<App />)
    await toggle('Lumen') // nothing else open: Lumen stays
    await toggle('Chat')
    await toggle('Lumen')
    await waitFor(() => expect(lumen()).toBeNull())

    // jsdom has no microphone, so Lumen refuses to start — and says why, with Lumen.
    await userEvent.click(screen.getByRole('button', { name: 'Let Lumen take over' }))
    await waitFor(() => expect(lumen()).not.toBeNull())
    expect(chatColumn()).toBeNull()
    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toMatch(/Lumen needs a secure page|cannot record/),
    )
  })

  it("the dock's mode button switches on a click, and Settings opens on a click", async () => {
    // React Bits' dock opens an item's menu only on a hold; a plain click did
    // nothing for these two while they had no action of their own.
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Work — switch to Personal' }))
    await waitFor(() => expect(lumen()?.textContent).toMatch(/What’s on your mind\?/))
    expect(auth.modes.at(-1)).toBe('personal')

    await userEvent.click(screen.getByRole('button', { name: 'Settings' }))
    expect(await screen.findByRole('dialog', { name: /Settings/ })).toBeDefined()
  })
})

describe('the two modes', () => {
  it('opens where the account says, and names that graph before the first request', async () => {
    auth.me.default_mode = 'personal'
    try {
      render(<App />)
      await waitFor(() => expect(lumen()).not.toBeNull())
      expect(auth.modes[0]).toBe('personal')
      expect(screen.getByRole('radio', { name: 'Personal' }).getAttribute('aria-checked')).toBe('true')
      await waitFor(() => expect(lumen()?.textContent).toMatch(/What’s on your mind\?/))
      expect(document.documentElement.dataset.mode).toBe('personal')
    } finally {
      auth.me.default_mode = 'workspace'
    }
  })

  it('switches everything together, with a fresh conversation, and remembers the choice', async () => {
    render(<App />)
    await toggle('Chat')
    await waitFor(() => expect(within(chatColumn()!).getByText(/What are you working on\?/)).toBeDefined())
    expect(screen.getByRole('button', { name: 'Share what I say with the team' })).toBeDefined()

    await userEvent.click(screen.getByRole('radio', { name: 'Personal' }))

    // A fresh workspace opens on Lumen again, in the other memory.
    await waitFor(() => expect(lumen()?.textContent).toMatch(/What’s on your mind\?/))
    expect(screen.queryByText(/What are you working on\?/)).toBeNull() // no history carried across
    await toggle('Chat')
    expect(screen.queryByRole('button', { name: 'Share what I say with the team' })).toBeNull()
    expect(auth.modes.at(-1)).toBe('personal')
    expect(localStorage.getItem('continuum.mode.mark')).toBe('personal')
  })

  it('asks a new instance what it is for, and opens there', async () => {
    auth.state.needsSetup = true
    auth.state.signedIn = false
    render(<App />)
    await waitFor(() => expect(screen.getByRole('radio', { name: 'Both' })).toBeDefined())

    await userEvent.click(screen.getByRole('radio', { name: 'Work' }))
    await userEvent.type(screen.getByLabelText('Email'), 'mark@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'correct horse battery staple')
    await userEvent.type(screen.getByLabelText('Confirm password'), 'correct horse battery staple')
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }))

    await waitFor(() => expect(auth.setup).toHaveBeenCalled())
    expect(auth.setup.mock.calls[0]).toEqual([
      'mark@example.com', 'correct horse battery staple', undefined, 'workspace',
    ])
  })
})

describe('the Personal hint for existing Work accounts', () => {
  it('shows once, and trying it switches and never shows again', async () => {
    render(<App />)
    await toggle('Memories')
    await userEvent.click(await screen.findByRole('button', { name: 'Try Personal' }))
    await waitFor(() => expect(lumen()?.textContent).toMatch(/What’s on your mind\?/))
    expect(localStorage.getItem('continuum.personal_hint_seen')).toBe('1')
    await userEvent.click(screen.getByRole('radio', { name: 'Work' }))
    await waitFor(() => expect(lumen()?.textContent).toMatch(/What are you working on\?/))
    await toggle('Memories')
    expect(screen.queryByRole('button', { name: 'Try Personal' })).toBeNull()
  })
})
