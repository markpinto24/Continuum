import { describe, expect, it } from 'vitest'

import { describeEscalation, findEscalation } from './escalation'
import type { Escalation, Memory } from './types'

const esc = (over: Partial<Escalation>): Escalation => ({
  target_id: 'old', judge_relation: 'supersedes', judge_confidence: 0.64, similarity: 0.8,
  gate: 0.8, forced: false, reason: '', raised_at: '2026-09-27T00:00:00Z', ...over,
})
const mem = (id: string, escalations: Escalation[] = []) => ({ id, escalations }) as unknown as Memory

describe('findEscalation', () => {
  it('finds the escalation between the two sides, from either one', () => {
    expect(findEscalation([mem('old'), mem('new', [esc({})])])?.target_id).toBe('old')
  })

  it('ignores escalations against memories outside this dispute', () => {
    expect(findEscalation([mem('old'), mem('new', [esc({ target_id: 'elsewhere' })])])).toBeNull()
  })
})

describe('describeEscalation', () => {
  it('explains a supersede held back by the gate', () => {
    expect(describeEscalation(esc({}))).toBe(
      'The resolver thought the newer statement replaces the older (0.64), but that is under the 0.80 bar for acting alone, so it asked you.',
    )
  })

  it('explains the role rule', () => {
    expect(describeEscalation(esc({ forced: true, judge_confidence: 1 }))).toContain('role that is often shared')
  })

  it('explains a partial conflict', () => {
    expect(describeEscalation(esc({ judge_relation: 'conflict', judge_confidence: 0.9 }))).toContain('(0.90)')
  })

  it('says nothing when nothing was recorded', () => {
    expect(describeEscalation(null)).toBeNull()
  })
})
