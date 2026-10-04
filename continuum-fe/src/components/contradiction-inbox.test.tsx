import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ContradictionInbox } from './contradiction-inbox'
import type { Resource } from '@/hooks/use-resource'
import type { ConflictListResponse, GateEvidence, Memory } from '@/lib/types'
import { makeMemory } from '@/test/fixtures'

const resolveConflict = vi.fn()
const resolveTeamConflict = vi.fn()
const feedbackEvidence = vi.fn()
const rules = vi.fn()
const approveRule = vi.fn()

vi.mock('@/lib/api', () => ({
  api: {
    resolveConflict: (...args: unknown[]) => resolveConflict(...args),
    resolveTeamConflict: (...args: unknown[]) => resolveTeamConflict(...args),
    feedbackEvidence: (...args: unknown[]) => feedbackEvidence(...args),
    feedbackExportUrl: '/api/v1/feedback/export',
    feedbackExportUrlFor: (kind: string) => `/api/v1/feedback/export?kind=${kind}`,
    rules: () => rules(),
    approveRule: (...args: unknown[]) => approveRule(...args),
    revokeRule: vi.fn(),
    calibration: () =>
      Promise.resolve({ labels: 0, points: [], usable: false, in_use: false, min_labels: 30 }),
    feedbackSummary: () =>
      Promise.resolve({ decisions: 0, rejected_facts: 0, answers_up: 0, answers_down: 0, missing_memories: 0 }),
  },
}))

function memory(id: string, content: string): Memory {
  return makeMemory({ id, content, status: 'contradicted' })
}

const postgres = memory('m-postgres', 'Atlas runs on Postgres')
const mongo = memory('m-mongo', 'Atlas runs on Mongo')

function resource(data: ConflictListResponse | null): Resource<ConflictListResponse> {
  return { data, error: null, loading: false, refresh: vi.fn() }
}

const ONE_DISPUTE = resource({
  total: 1,
  conflicts: [{ memory: postgres, conflicting: [mongo], team: false }],
})

beforeEach(() => {
  rules.mockReset().mockResolvedValue({ rules: [], suggestions: [] })
  approveRule.mockReset().mockResolvedValue({})
  resolveTeamConflict.mockReset().mockResolvedValue({})
  feedbackEvidence.mockResolvedValue(evidence({}))
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


function evidence(over: Partial<GateEvidence>): GateEvidence {
  return {
    gate: 0.8, labels: 0, with_judgement: 0, bands: [], below_gate_total: 0,
    below_gate_refuted: 0, error_upper_bound: null, role_rule_total: 0, role_rule_shared: 0,
    conflicts_total: 0, conflicts_both_hold: 0,
    recommendation: 'Keep 0.80. No escalated supersedes yet.', ...over,
  }
}

describe('learning from decisions', () => {
  it('explains why a pair is in the inbox, from what the resolver recorded', () => {
    const newer = {
      ...memory('m2', 'Atlas runs on Mongo'),
      escalations: [{
        target_id: 'm1', judge_relation: 'supersedes' as const, judge_confidence: 0.64,
        similarity: 0.8, gate: 0.8, forced: false, reason: '', raised_at: '2026-09-27T00:00:00Z',
      }],
    }
    render(
      <ContradictionInbox
        resource={resource({ total: 1, conflicts: [{ memory: memory('m1', 'Atlas runs on Postgres'), conflicting: [newer], team: false }] })}
        onResolved={vi.fn()}
        onSelect={vi.fn()}
      />,
    )
    expect(screen.getByText(/under the 0\.80 bar for acting alone/)).toBeDefined()
  })

  it('reports what the decisions say about the gate, with an export', async () => {
    feedbackEvidence.mockResolvedValue(
      evidence({ labels: 4, below_gate_total: 3, recommendation: 'Keep 0.80 for now. All 3 were right.' }),
    )
    render(<ContradictionInbox resource={resource({ total: 0, conflicts: [] })} onResolved={vi.fn()} onSelect={vi.fn()} />)

    expect(await screen.findByText('Keep 0.80 for now. All 3 were right.')).toBeDefined()
    expect(screen.getByText(/4 decisions recorded/)).toBeDefined()
    expect(screen.getByRole('link', { name: /export as test cases/i }).getAttribute('href')).toBe(
      '/api/v1/feedback/export',
    )
  })
})

describe('team disputes', () => {
  const mine = makeMemory({ id: 'mine', content: 'Atlas uses Mongo', status: 'contradicted', team_conflicts_with: ['team'] })
  const team = makeMemory({ id: 'team', user_id: '_shared', content: 'Atlas uses Postgres', shared_by_email: 'sara@x.io' })

  it('offers the team, mine and both — and settles only my side', async () => {
    const onResolved = vi.fn()
    render(
      <ContradictionInbox
        resource={resource({ total: 1, conflicts: [{ memory: mine, conflicting: [team], team: true }] })}
        onResolved={onResolved}
        onSelect={vi.fn()}
      />,
    )
    expect(screen.getByText(/disagrees with what the team holds/)).toBeDefined()
    expect(screen.getByText('Team · shared by sara@x.io')).toBeDefined()
    await userEvent.click(screen.getByRole('button', { name: /mine holds/i }))
    await waitFor(() => expect(resolveTeamConflict).toHaveBeenCalledWith('mine', 'team', 'mine_holds'))
    expect(onResolved).toHaveBeenCalled()
  })
})

describe('the learning panel', () => {
  it('pools everyone’s decisions on request', async () => {
    feedbackEvidence.mockResolvedValue(evidence({ labels: 2 }))
    render(<ContradictionInbox resource={resource({ total: 0, conflicts: [] })} onResolved={vi.fn()} onSelect={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Everyone' }))
    await waitFor(() => expect(feedbackEvidence).toHaveBeenLastCalledWith('team'))
  })

  it('suggests a rule, applies it only when asked, and an admin-only team rule says so', async () => {
    feedbackEvidence.mockResolvedValue(evidence({ labels: 3 }))
    rules.mockResolvedValue({
      rules: [],
      suggestions: [
        { owner: 'mark', subject: 'rotation', both_hold: 3 },
        { owner: '_shared', subject: 'on-call', both_hold: 2 },
      ],
    })
    render(<ContradictionInbox resource={resource({ total: 0, conflicts: [] })} onResolved={vi.fn()} onSelect={vi.fn()} />)
    expect(await screen.findByText('“rotation”')).toBeDefined()
    expect(approveRule).not.toHaveBeenCalled()
    expect(screen.getByText('An admin can apply team rules.')).toBeDefined()
    await userEvent.click(screen.getByRole('button', { name: /apply rule/i }))
    expect(approveRule).toHaveBeenCalledWith('rotation', 'mine')
  })
})
