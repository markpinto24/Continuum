import { api } from './api'
import type { Memory, Question, QuestionOption } from './types'

/**
 * Answering a pending item — "maybe", kept, never believed (requirements.md
 * Part 2). Shared by the chat's question cards, the inbox and Lumen, so a yes
 * means the same thing wherever it is said.
 */

const OUTCOME: Record<QuestionOption['action'], string> = {
  confirm: 'Settled — it is part of your memory now.',
  unsure: 'Still undecided. I will ask again when it comes up.',
  decline: 'Noted — I will not bring it up again.',
}


/** One answer to a pending item; the sentence to show for it. Shared with Lumen. */
export async function answerPending(
  memoryId: string,
  action: QuestionOption['action'],
): Promise<string> {
  if (action === 'confirm') {
    const result = await api.confirmPending(memoryId)
    if (result.superseded.length) return 'Settled — and it replaces what you did before.'
    if (result.conflicts_raised.length) {
      return 'Settled — but it disagrees with something you said before; it is in your inbox.'
    }
    if (result.reinforced.length) return 'Settled — you had already told me this.'
    return OUTCOME.confirm
  }
  if (action === 'decline') await api.declinePending(memoryId)
  else await api.unsurePending(memoryId)
  return OUTCOME[action]
}

/** The same question for a pending memory listed outside a chat turn (the inbox). */
export function questionFor(memory: Memory): Question {
  const day = new Date(memory.created_at).toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
  })
  return {
    kind: 'pending',
    memory_id: memory.id,
    text: `You were still deciding on this (${day}): ${memory.content}`,
    options: [
      { label: "Yes, I'm going with it", action: 'confirm' },
      { label: 'Still unsure', action: 'unsure' },
      { label: 'No', action: 'decline' },
    ],
  }
}
