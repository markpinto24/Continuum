import type { Memory } from '@/lib/types'

/** A complete Memory for tests; override only what the test is about. */
export function makeMemory(over: Partial<Memory> = {}): Memory {
  const at = over.created_at ?? '2026-03-04T00:00:00Z'
  return {
    id: 'm1',
    user_id: 'mark',
    content: 'Atlas runs on Postgres',
    category: 'decision',
    subject: 'atlas',
    confidence: 0.7,
    status: 'active',
    source_id: null,
    source_excerpt: null,
    supersedes: [],
    superseded_by: null,
    conflicts_with: [],
    team_conflicts_with: [],
    escalations: [],
    shared_by: null,
    shared_by_email: null,
    shared_as: null,
    kind: 'fact',
    derived_from: [],
    rejected_reason: null,
    redacted_at: null,
    superseded_at: null,
    archived_at: null,
    created_at: at,
    updated_at: at,
    last_reinforced_at: at,
    reinforcement_count: 0,
    ...over,
  }
}
