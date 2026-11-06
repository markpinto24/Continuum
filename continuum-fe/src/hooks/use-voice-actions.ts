import { useCallback, useMemo, useRef } from 'react'

import { api } from '@/lib/api'
import type { ConflictPair, Memory } from '@/lib/types'
import {
  type DisputeChoice,
  type MemoryAction,
  announce,
  askWhich,
  consent,
  describeDispute,
  disputeChoice,
  eligible,
  forgetConsent,
  isShort,
  pickOne,
  quote,
  readBack,
  voiceCommand,
  wantsNext,
} from '@/lib/voice-actions'

/**
 * Lumen acting on your memory when you ask it aloud: settling a dispute,
 * marking a memory still true, setting one aside as not a real fact, forgetting
 * one. The words are read by `lib/voice-actions.ts`; this is the conversation.
 *
 * Its one rule: Lumen says exactly what it will do, then waits. Only a yes to
 * that does it. A no, silence, or anything else leaves your memory as it was —
 * and says so. What Lumen is waiting for lasts for the next thing said only,
 * as with its question about a pending item.
 */

/** Below this, a search result is not the memory meant (raw cosine, bge-m3). */
const MIN_MATCH = 0.45
/** Within this of the best, a result is as likely to be the one meant: ask which. */
const CLOSE_MATCH = 0.05

type Awaiting =
  | { kind: 'dispute-choice'; pair: ConflictPair; tries: number }
  | { kind: 'dispute-confirm'; pair: ConflictPair; choice: Exclude<DisputeChoice, 'skip'>; words: string }
  | { kind: 'dispute-next' }
  | { kind: 'memory-pick'; action: MemoryAction; candidates: Memory[]; tries: number }
  | { kind: 'memory-confirm'; action: MemoryAction; memory: Memory; tries: number }

const UNCHANGED = 'I haven’t changed anything.'

export function useVoiceActions({
  say,
  note,
  onBusy,
  onGraphChanged,
  lastCited,
}: {
  /** Speak, and resolve when it has been said. */
  say: (text: string) => Promise<void>
  /** Write the exchange into the chat's transcript. `said` null: Lumen alone. */
  note: (said: string | null, reply: string) => void
  /** Lumen is working (an API call), not speaking. */
  onBusy: () => void
  onGraphChanged: () => void
  /** The memories the last answer stood on: what "that" refers to. */
  lastCited: () => string[]
}) {
  const awaiting = useRef<Awaiting | null>(null)
  // Disputes passed over this session: not read out again until asked afresh.
  const skipped = useRef(new Set<string>())

  const reply = useCallback(
    async (said: string | null, text: string) => {
      note(said, text)
      await say(text)
    },
    [note, say],
  )

  // --- Disputes --------------------------------------------------------------

  const readNextDispute = useCallback(
    async (said: string | null, prefix = '') => {
      onBusy()
      const { conflicts } = await api.conflicts()
      const waiting = conflicts.filter(
        (pair) => pair.conflicting.length > 0 && !skipped.current.has(pair.memory.id),
      )
      const [pair] = waiting
      if (!pair) {
        const none =
          skipped.current.size > 0
            ? 'Nothing else is waiting — the ones you skipped stay in your inbox.'
            : 'No disagreements are waiting for you.'
        await reply(said, `${prefix}${none}`)
        return
      }
      awaiting.current = { kind: 'dispute-choice', pair, tries: 0 }
      await reply(said, `${prefix}${describeDispute(pair, 1, waiting.length)}`)
    },
    [onBusy, reply],
  )

  const settleDispute = useCallback(
    async (pair: ConflictPair, choice: Exclude<DisputeChoice, 'skip'>, words: string) => {
      const [other] = pair.conflicting
      onBusy()
      if (pair.team) {
        const decision = choice === 'first' ? 'mine_holds' : choice === 'second' ? 'team_holds' : 'both_hold'
        await api.resolveTeamConflict(pair.memory.id, other.id, decision, { said: words })
      } else {
        const [winner, loser] = choice === 'second' ? [other, pair.memory] : [pair.memory, other]
        await api.resolveConflict({
          winner_id: winner.id,
          loser_ids: [loser.id],
          keep_both: choice === 'both',
          said: words,
          spoken: true,
        })
      }
      onGraphChanged()
    },
    [onBusy, onGraphChanged],
  )

  // --- One memory ------------------------------------------------------------

  const perform = useCallback(
    async (action: MemoryAction, memory: Memory, words: string): Promise<string> => {
      onBusy()
      let done: string
      if (action === 'still_true') {
        await api.reinforce(memory.id)
        done = `Done — ${quote(memory)} is marked as still true.`
      } else if (action === 'not_a_fact') {
        const { restored } = await api.rejectMemory(
          memory.id,
          'not_a_fact',
          `Said to Lumen: ${words}`.slice(0, 500),
        )
        done = restored.length
          ? `Done — set aside, and ${restored.length === 1 ? 'the memory it had replaced is' : `${restored.length} memories it had replaced are`} back.`
          : 'Done — set aside as not a real fact. You can restore it from its panel.'
      } else {
        // Never the words that asked for it: they may say what was forgotten.
        await api.forgetMemory(memory.id)
        done = 'Forgotten. Its words are gone.'
      }
      onGraphChanged()
      return done
    },
    [onBusy, onGraphChanged],
  )

  const findMemory = useCallback(
    async (said: string, action: MemoryAction, target: string | null) => {
      onBusy()
      let candidates: Memory[]
      if (target === null) {
        const ids = lastCited()
        if (ids.length === 0) {
          await reply(said, 'Which memory do you mean? Say it with what it says.')
          return
        }
        const found = await Promise.all(ids.slice(0, 3).map((id) => api.memory(id).catch(() => null)))
        candidates = found.filter((m): m is Memory => m !== null && eligible(action, m))
      } else {
        const { results } = await api.searchMemories(target, 6)
        const usable = results.filter((r) => r.score >= MIN_MATCH && eligible(action, r.memory))
        const best = usable[0]?.score ?? 0
        candidates = usable.filter((r) => r.score >= best - CLOSE_MATCH).slice(0, 3).map((r) => r.memory)
      }
      if (candidates.length === 0) {
        await reply(said, `I couldn't find a memory like that. ${UNCHANGED}`)
        return
      }
      if (candidates.length === 1) {
        const [memory] = candidates
        awaiting.current = { kind: 'memory-confirm', action, memory, tries: 0 }
        await reply(said, announce(action, memory))
        return
      }
      awaiting.current = { kind: 'memory-pick', action, candidates, tries: 0 }
      await reply(said, askWhich(candidates))
    },
    [lastCited, onBusy, reply],
  )

  // --- The conversation ------------------------------------------------------

  /** The answer to what Lumen asked. False: it was not one — handle it as usual. */
  const answer = useCallback(
    async (waiting: Awaiting, said: string): Promise<boolean> => {
      // Not understood: a short reply gets the question once more; a long one
      // was the next thing the person wanted to say.
      const unclear = async (again: Awaiting | null, ask: string, leave: string) => {
        if (again && isShort(said)) {
          awaiting.current = again
          await reply(said, ask)
          return true
        }
        if (isShort(said)) {
          await reply(said, leave)
          return true
        }
        note(null, leave)
        await say(leave)
        return false
      }

      switch (waiting.kind) {
        case 'dispute-choice': {
          const choice = disputeChoice(said)
          if (choice === 'skip') {
            skipped.current.add(waiting.pair.memory.id)
            await readNextDispute(said, 'Skipped — it stays in your inbox. ')
            return true
          }
          if (choice) {
            awaiting.current = { kind: 'dispute-confirm', pair: waiting.pair, choice, words: said }
            await reply(said, readBack(waiting.pair, choice))
            return true
          }
          return unclear(
            waiting.tries < 1 ? { ...waiting, tries: waiting.tries + 1 } : null,
            waiting.pair.team
              ? 'I didn’t catch which. Say yours, the team’s, or both — or skip.'
              : 'I didn’t catch which. Say the first, the second, or both — or skip.',
            `I’ll leave it in your inbox. ${UNCHANGED}`,
          )
        }
        case 'dispute-confirm': {
          const yes = consent(said)
          if (yes !== 'yes') {
            return unclear(null, '', `Okay — ${UNCHANGED} It stays in your inbox.`)
          }
          try {
            await settleDispute(waiting.pair, waiting.choice, `${waiting.words} — ${said}`)
          } catch (cause) {
            const why = cause instanceof Error ? cause.message : String(cause)
            await reply(said, `I couldn’t settle it: ${why}. ${UNCHANGED}`)
            return true
          }
          skipped.current.delete(waiting.pair.memory.id)
          onBusy()
          const { conflicts } = await api.conflicts()
          const left = conflicts.filter(
            (pair) => pair.conflicting.length > 0 && !skipped.current.has(pair.memory.id),
          ).length
          if (left > 0) {
            awaiting.current = { kind: 'dispute-next' }
            await reply(
              said,
              `Settled. ${left === 1 ? 'One more is' : `${left} more are`} waiting — say next to hear it, or ask me anything.`,
            )
          } else {
            await reply(said, 'Settled. That was the last one.')
          }
          return true
        }
        case 'dispute-next': {
          if (!wantsNext(said)) return false
          await readNextDispute(said)
          return true
        }
        case 'memory-pick': {
          const pick = pickOne(said, waiting.candidates.length)
          if (pick === 'none') {
            await reply(said, `Okay — ${UNCHANGED}`)
            return true
          }
          if (pick !== null) {
            const memory = waiting.candidates[pick]
            awaiting.current = { kind: 'memory-confirm', action: waiting.action, memory, tries: 0 }
            await reply(said, announce(waiting.action, memory))
            return true
          }
          return unclear(
            waiting.tries < 1 ? { ...waiting, tries: waiting.tries + 1 } : null,
            'I didn’t catch which. Say the first, the second, or none.',
            `Okay — ${UNCHANGED}`,
          )
        }
        case 'memory-confirm': {
          const forgetting = waiting.action === 'forget'
          const yes = forgetting ? forgetConsent(said) : consent(said)
          if (yes === 'yes') {
            try {
              await reply(said, await perform(waiting.action, waiting.memory, said))
            } catch (cause) {
              const why = cause instanceof Error ? cause.message : String(cause)
              await reply(said, `That didn’t work: ${why}. ${UNCHANGED}`)
            }
            return true
          }
          // A plain yes to forgetting: say once more what it takes.
          if (forgetting && yes === null && consent(said) === 'yes' && waiting.tries < 1) {
            awaiting.current = { ...waiting, tries: waiting.tries + 1 }
            await reply(said, `To forget it I need you to say: yes, forget it. ${UNCHANGED}`)
            return true
          }
          return unclear(null, '', `Okay — ${UNCHANGED}`)
        }
      }
    },
    [note, onBusy, perform, readNextDispute, reply, say, settleDispute],
  )

  /**
   * Something was said to Lumen. True when it was an answer to Lumen, or a
   * request Lumen acted on; false when it is a question for the chat.
   */
  const handle = useCallback(
    async (said: string): Promise<boolean> => {
      const waiting = awaiting.current
      awaiting.current = null
      try {
        if (waiting && (await answer(waiting, said))) return true
        const command = voiceCommand(said)
        if (!command) return false
        if (command.kind === 'disputes') {
          skipped.current.clear()
          await readNextDispute(said)
        } else {
          await findMemory(said, command.kind, command.target)
        }
        return true
      } catch (cause) {
        // Failing toward the person: whatever went wrong, nothing was decided.
        awaiting.current = null
        const why = cause instanceof Error ? cause.message : String(cause)
        await reply(said, `Something went wrong: ${why}. ${UNCHANGED}`)
        return true
      }
    },
    [answer, findMemory, readNextDispute, reply],
  )

  /** Lumen stopped: nothing it asked is still waiting for an answer. */
  const reset = useCallback(() => {
    awaiting.current = null
    skipped.current.clear()
  }, [])

  return useMemo(() => ({ handle, reset }), [handle, reset])
}
