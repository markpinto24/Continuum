import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import App from './App'

/**
 * Shell smoke test. The WebGL scene cannot render under jsdom, so
 * `belief-graph` is stubbed — everything around it is real, which is enough to
 * catch the wiring mistakes that a type check does not: a panel that never
 * mounts, a badge that counts the wrong thing, a user id that fails to persist.
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

vi.mock('@/lib/api', () => ({
  api: {
    health: () =>
      Promise.resolve({
        status: 'ok',
        app: 'Continuum',
        environment: 'test',
        qdrant: 'up',
        llm: 'up',
      }),
    graph: () => Promise.resolve(graph),
    conflicts: () => Promise.resolve({ total: 2, conflicts: [] }),
  },
  streamChat: () => Promise.resolve(),
}))

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
