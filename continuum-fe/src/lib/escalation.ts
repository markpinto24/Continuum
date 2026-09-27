import type { Disagreement, Escalation, Memory } from './types'

/**
 * Why the resolver asked, in a sentence — from the escalation ingest recorded.
 *
 * Seeing the system's reasoning makes the decision easier, and makes a
 * systematic mistake visible: "it keeps calling these partial conflicts, and I
 * keep saying both are true" is exactly what the learning panel then counts.
 */
export function findEscalation(sides: Memory[]): Escalation | null {
  const ids = new Set(sides.map((s) => s.id))
  for (const side of sides) {
    const match = [...(side.escalations ?? [])].reverse().find((e) => ids.has(e.target_id))
    if (match) return match
  }
  return null
}

export function describeEscalation(escalation: Escalation | null): string | null {
  if (!escalation) return null
  const p = escalation.judge_confidence
  const at = p === null ? '' : ` (${p.toFixed(2)})`
  if (escalation.forced) {
    return 'Asked because this names a new holder for a role that is often shared, and nothing said the earlier one stopped.'
  }
  if (escalation.judge_relation === 'supersedes') {
    return `The resolver thought the newer statement replaces the older${at}, but that is under the ${escalation.gate.toFixed(2)} bar for acting alone, so it asked you.`
  }
  if (escalation.judge_relation === 'conflict') {
    return `The resolver judged these to overlap without one replacing the other${at} — only you can say which holds.`
  }
  return null
}

/** Order-independent identity for a dispute, so every banner showing it agrees. */
export function disputeKey(group: Disagreement): string {
  return group.memories
    .map((m) => m.id)
    .sort()
    .join('|')
}
