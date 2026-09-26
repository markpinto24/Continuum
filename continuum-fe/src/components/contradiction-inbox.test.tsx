import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ContradictionInbox } from './contradiction-inbox'
import type { Resource } from '@/hooks/use-resource'
import type { ConflictListResponse, Memory } from '@/lib/types'

const resolveConflict = vi.fn()

vi.mock('@/lib/api', () => ({
  api: {
    resolveConflict: (...args: unknown[]) => resolveConflict(...args),
  },
}))

function memory(id: string, content: string): Memory {
  return {
    id,
    user_id: 'mark',
    content,
    category: 'decision',
    subject: 'atlas',
    confidence: 0.7,
    status: 'contradicted',
    source_id: null,
    source_excerpt: null,
    supersedes: [],
    superseded_by: null,
    conflicts_with: [],
    created_at: '2026-03-04T00:00:00Z',
    updated_at: '2026-03-04T00:00:00Z',
    last_reinforced_at: '2026-03-04T00:00:00Z',
    reinforcement_count: 0,
  }
}

const postgres = memory('m-postgres', 'Atlas runs on Postgres')
const mongo = memory('m-mongo', 'Atlas runs on Mongo')

function resource(data: ConflictListResponse | null): Resource<ConflictListResponse> {
  return { data, error: null, loading: false, refresh: vi.fn() }
}

const ONE_DISPUTE = resource({
  total: 1,
  conflicts: [{ memory: postgres, conflicting: [mongo] }],
})

beforeEach(() => {
  resolveConflict.mockReset()
  resolveConflict.mockResolvedValue({ winner: postgres, losers: [mongo], action: 'kept_both' })
})

describe('ContradictionInbox', () => {
  it('shows both sides of a dispute', () => {
    render(
      <ContradictionInbox
        resource={ONE_DISPUTE}
        onResolved={vi.fn()}
        onSelect={vi.fn()}
      />,
    )

    expect(screen.getByText('Atlas runs on Postgres')).toBeDefined()
    expect(screen.getByText('Atlas runs on Mongo')).toBeDefined()
  })

  it('sends keep_both when both beliefs are true', async () => {
    // Not a cop-out verdict: plenty of apparent contradictions are two things
    // that are simply both true, and the UI has to be able to say so.
    render(
      <ContradictionInbox
        resource={ONE_DISPUTE}
        onResolved={vi.fn()}
        onSelect={vi.fn()}
      />,
    )

    await userEvent.click(screen.getByRole('button', { name: /both are true/i }))

    await waitFor(() =>
      expect(resolveConflict).toHaveBeenCalledWith({
        winner_id: postgres.id,
        loser_ids: [mongo.id],
        keep_both: true,
      }),
    )
  })

  it('names the clicked side as the winner, not always the first', async () => {
    render(
      <ContradictionInbox
        resource={ONE_DISPUTE}
        onResolved={vi.fn()}
        onSelect={vi.fn()}
      />,
    )

    // The second "This one holds" belongs to the Mongo side.
    const buttons = screen.getAllByRole('button', { name: /this one holds/i })
    await userEvent.click(buttons[1])

    await waitFor(() =>
      expect(resolveConflict).toHaveBeenCalledWith({
        winner_id: mongo.id,
        loser_ids: [postgres.id],
        keep_both: false,
      }),
    )
  })

  it('refreshes the graph after a verdict is applied', async () => {
    const onResolved = vi.fn()
    render(
      <ContradictionInbox
        resource={ONE_DISPUTE}
        onResolved={onResolved}
        onSelect={vi.fn()}
      />,
    )

    await userEvent.click(screen.getAllByRole('button', { name: /this one holds/i })[0])

    await waitFor(() => expect(onResolved).toHaveBeenCalled())
  })

  it('says so plainly when there is nothing to decide', () => {
    render(
      <ContradictionInbox
        resource={resource({ total: 0, conflicts: [] })}
        onResolved={vi.fn()}
        onSelect={vi.fn()}
      />,
    )

    expect(screen.getByText(/nothing to decide/i)).toBeDefined()
  })
})
