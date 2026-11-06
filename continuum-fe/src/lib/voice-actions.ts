/**
 * What Lumen can do to your memory when you ask it aloud — settle a dispute,
 * mark a memory still true, set one aside as not a real fact, forget one — in
 * small, pure pieces: how a request is recognised, how an answer is read, and
 * what Lumen says.
 *
 * Every one of them is read from the words, never by a model reading intent:
 * a model judging what you meant would be the second LLM call arbitrating a
 * contradiction that commitment 2 rules out. And every one ends the same way —
 * Lumen says exactly what it will do, and does it only on a yes. A no, silence,
 * or anything it cannot place leaves your memory as it was.
 *
 * Whisper mishears names and numbers more than anything else, which are exactly
 * what tell two sides of a dispute apart: that is why the read-back exists.
 */

import type { ConflictPair, Memory } from './types'

const NAME = '(?:lumen|lumin|luman|lumens|loomen|lumon|lumeen)'

/** Lowercase words only, the name and politeness taken out, "isn't" -> "is not". */
export function plainWords(text: string): string {
  return text
    .toLowerCase()
    .replace(/[’']/g, "'")
    .replace(/\b(is|are|was|do|does|did)n't\b/g, '$1 not')
    .replace(/[^a-z0-9\s]/g, ' ')
    .replace(new RegExp(`(?:^| )${NAME}(?= |$)`, 'g'), ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/^(?:(?:hey|ok|okay|so|um|uh|please|can you|could you|would you|will you) )+/, '')
    .replace(/ please$/, '')
    .trim()
}

const wordCount = (text: string) => (text ? text.split(' ').length : 0)

/** A short reply that was not understood is worth asking again; a long one is
 *  probably the next thing the person wants to say. */
export function isShort(text: string): boolean {
  return wordCount(plainWords(text)) <= 5
}

// --- Requests ------------------------------------------------------------------

export type MemoryAction = 'still_true' | 'not_a_fact' | 'forget'

export type VoiceCommand =
  | { kind: 'disputes' }
  /** `target` is what the memory says, as spoken; null means "the one we just talked about". */
  | { kind: MemoryAction; target: string | null }

/** "that", "it", "this": the memory the last answer stood on. */
const POINTING = /^(?:that|it|this|that one|this one|that memory|this memory|the last one)$/

const target = (phrase: string | undefined): string | null => {
  const words = (phrase ?? '').replace(/^(?:that|the fact that|the memory that) /, '').trim()
  return !words || POINTING.test(words) ? null : words
}

const DISPUTE_NOUN = /(?:^| )(?:disputes?|disagreements?|conflicts?|contradictions?)(?: |$)/
// Asked, or told — never merely mentioned ("I had a dispute with my landlord").
const DISPUTE_ASK =
  /^(?:what|which|how many|do i have|are there|is there|any|tell me|read|go through|let s|lets|let us|settle|resolve|review|show|open|decide|help me|i want to settle|i d like to settle)(?: |$)/

// What Lumen's greeting tells you to say: "let's settle them".
const SETTLE_THEM =
  /^(?:let s |lets |let us |i want to |i d like to )?(?:settle|resolve|go through|sort out) (?:them|it|those|these|both)(?: now| together)?$/

const STILL_TRUE = [
  /^(?:yes |yeah )?(?:that is |it is |this is |that s |it s )?still true$/,
  /^(?:it is|it s) still true that (.+)$/,
  /^(?:mark|confirm) (.+?) as still true$/,
  /^(.+?) (?:is|are|s) still true$/,
]
const NOT_A_FACT = [
  /^(?:that is |it is |this is |that s |it s )?not (?:a )?real fact$/,
  /^(.+?) (?:is|are|s) not (?:a )?real facts?$/,
  /^i never said (.+)$/,
]
// "Forget it" and "never mind" are how people say "leave it": never a command.
const FORGET = /^(?:forget|erase) (?:about )?(.+)$/
const NOT_FORGET = /^(?:forget|erase) (?:it|about it|that it|everything|all of it)$/

/**
 * A request Lumen acts on, recognised only as the whole of what was said and
 * only in its own forms: "forget that I'm 22", "my partner is Priya is still
 * true", "that's not a real fact", "let's go through the disagreements".
 * Anything else is a question for the chat.
 */
export function voiceCommand(text: string): VoiceCommand | null {
  const said = plainWords(text)
  if (!said) return null
  if ((DISPUTE_NOUN.test(said) && DISPUTE_ASK.test(said)) || SETTLE_THEM.test(said)) {
    return { kind: 'disputes' }
  }
  for (const pattern of STILL_TRUE) {
    const match = said.match(pattern)
    if (match) return { kind: 'still_true', target: target(match[1]) }
  }
  for (const pattern of NOT_A_FACT) {
    const match = said.match(pattern)
    if (match) return { kind: 'not_a_fact', target: target(match[1]) }
  }
  if (!NOT_FORGET.test(said)) {
    const match = said.match(FORGET)
    if (match) return { kind: 'forget', target: target(match[1]) }
  }
  return null
}

// --- Answers -------------------------------------------------------------------

export type DisputeChoice = 'first' | 'second' | 'both' | 'skip'

const has = (said: string, alternatives: string) =>
  new RegExp(`(?:^| )(?:${alternatives})(?: |$)`).test(said)

/**
 * Which side holds, read from the words. Naming both sides ("the first — no,
 * the second") is no answer: Lumen asks again rather than pick one.
 */
export function disputeChoice(text: string): DisputeChoice | null {
  const said = plainWords(text)
  if (!said) return null
  const first = has(said, 'first|1st|former|mine|my one|number one') || /^(?:the )?one$/.test(said)
  const second =
    has(said, 'second|2nd|latter|the team s|the teams|team s|number two') || /^(?:the )?two$/.test(said)
  const both = has(said, 'both|both of them|keep both|both are true|both true|both hold')
  const skip = has(said, 'skip|later|not now|pass|leave it|i do not know|i don t know|not sure|next')
  if (both && !first && !second) return 'both'
  if (first && !second && !both) return 'first'
  if (second && !first && !both) return 'second'
  if (skip && !first && !second && !both) return 'skip'
  return null
}

/** One of a short list Lumen read out: its index, 'none', or null. */
export function pickOne(text: string, count: number): number | 'none' | null {
  const said = plainWords(text)
  if (has(said, 'none|neither|none of them|never mind|nevermind|no')) return 'none'
  const picks = [
    has(said, 'first|1st|number one') || /^(?:the )?one$/.test(said),
    has(said, 'second|2nd|number two') || /^(?:the )?two$/.test(said),
    has(said, 'third|3rd|number three') || /^(?:the )?three$/.test(said),
  ]
  const chosen = picks.flatMap((picked, index) => (picked && index < count ? [index] : []))
  if (chosen.length === 1) return chosen[0]
  if (chosen.length === 0 && has(said, 'last|the last one') && count > 0) return count - 1
  return null
}

const YES =
  /^(?:yes|yeah|yep|yup|sure|ok|okay|do it|go ahead|please do|confirm|confirmed|correct|that s right|that is right|right|settle it)(?: |$)/
const NO =
  /^(?:no|nope|nah|don t|do not|cancel|stop|wait|wrong|never mind|nevermind|not|leave it|hold on)(?: |$)/

/**
 * Yes or no to what Lumen just read back. A no wins over a yes, and a "but"
 * makes it no answer at all ("yes, but the other one"): when in doubt, nothing
 * is changed.
 */
export function consent(text: string): 'yes' | 'no' | null {
  const said = plainWords(text)
  if (!said) return null
  if (NO.test(said)) return 'no'
  if (has(said, 'but|except|instead|other one')) return null
  if (YES.test(said)) return 'yes'
  return null
}

/**
 * Forgetting cannot be undone, so it takes more than a yes: "yes, forget it".
 * A misheard "yeah" — or "forget it", which people say to mean "leave it" — is
 * never enough.
 */
export function forgetConsent(text: string): 'yes' | 'no' | null {
  const said = plainWords(text)
  if (!said) return null
  if (NO.test(said)) return 'no'
  if (/^(?:yes|yeah|yep) (?:please )?(?:forget|erase)(?: it| that| them)?(?: now)?$/.test(said)) return 'yes'
  return null
}

/** "Next", or a yes, to hear the next dispute. */
export function wantsNext(text: string): boolean {
  return /^(?:next|next one|the next one|yes|yeah|go on|continue|go ahead|sure|ok|okay|keep going)(?: |$)/.test(
    plainWords(text),
  )
}

// --- What Lumen says -----------------------------------------------------------

/** A memory's words, short enough to say. */
export function quote(memory: Memory, max = 140): string {
  const text = memory.content.trim().replace(/\.$/, '')
  if (text.length <= max) return `“${text}”`
  const cut = text.slice(0, max)
  return `“${cut.slice(0, cut.lastIndexOf(' ') > 60 ? cut.lastIndexOf(' ') : max)}…”`
}

const ordinals = ['First', 'Second', 'Third']

/** Read in the order the inbox lists them: neither side first because it is newer. */
export function describeDispute(pair: ConflictPair, position: number, total: number): string {
  const [other] = pair.conflicting
  const about = pair.memory.subject ? `, about ${pair.memory.subject.replace(/-/g, ' ')}` : ''
  const which = total === 1 ? 'One disagreement is waiting' : `Disagreement ${position} of ${total}`
  if (pair.team) {
    return (
      `${which}${about} — yours against the team's. Yours: ${quote(pair.memory)}. ` +
      `The team's: ${quote(other)}. Which holds — yours, the team's, or both? Or say skip.`
    )
  }
  return (
    `${which}${about}. First: ${quote(pair.memory)}. Second: ${quote(other)}. ` +
    'Which holds — the first, the second, or both? Or say skip.'
  )
}

/** The decision, said back before anything changes. */
export function readBack(pair: ConflictPair, choice: Exclude<DisputeChoice, 'skip'>): string {
  const [other] = pair.conflicting
  const ask = ' Shall I settle it? Yes or no.'
  if (choice === 'both') return `So both are true, and both stay as they are.${ask}`
  if (pair.team) {
    return choice === 'first'
      ? `So yours holds: ${quote(pair.memory)} is shared with the team, and the team's memory decides from there.${ask}`
      : `So the team's holds: ${quote(other)}. Yours is kept as history, no longer believed.${ask}`
  }
  const [holds, kept] = choice === 'first' ? [pair.memory, other] : [other, pair.memory]
  return `So ${quote(holds)} holds, and ${quote(kept)} is kept as history, no longer believed.${ask}`
}

/** What Lumen will do to one memory, said before it does it. */
export function announce(action: MemoryAction, memory: Memory): string {
  switch (action) {
    case 'still_true':
      return `I'll mark ${quote(memory)} as still true — that raises how sure I am of it. Shall I? Yes or no.`
    case 'not_a_fact':
      return (
        `I'll set ${quote(memory)} aside as not a real fact: it's archived, and anything it ` +
        'replaced comes back. You can restore it later. Shall I? Yes or no.'
      )
    case 'forget':
      return (
        `Forgetting erases ${quote(memory)} for good — its words are wiped everywhere, and it ` +
        'cannot be undone. If you are sure, say: yes, forget it. Anything else leaves it.'
      )
  }
}

/** Several memories fit what was said: read them, and ask which. */
export function askWhich(memories: Memory[]): string {
  const listed = memories.map((memory, i) => `${ordinals[i]}: ${quote(memory, 100)}.`).join(' ')
  const options = memories.length === 2 ? 'the first or the second' : 'the first, the second, or the third'
  return `I found ${memories.length} that could be it. ${listed} Which one — ${options}? Or say none.`
}

/** Can this action be taken on this memory at all? */
export function eligible(action: MemoryAction, memory: Memory, sharedSpace = '_shared'): boolean {
  if (memory.redacted_at || memory.user_id === sharedSpace) return false
  if (action === 'forget') return true
  const believed = memory.status === 'active' || memory.status === 'contradicted'
  // A "maybe" is never believed, so it cannot be still true; it can be a misreading.
  return action === 'still_true' ? believed : believed || memory.status === 'pending'
}
