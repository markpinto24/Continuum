/**
 * Lumen — the hands-free voice mode — in small, pure pieces, kept out of the
 * component so they are testable.
 *
 * Commands are matched against Whisper's transcript, so they tolerate how it
 * spells the name: tested across four voices, "Lumen" came back right 11 times
 * in 12 and once as "Lumin". (Lumen was chosen because no common word sounds
 * like it — "Avery" would have collided with "every".)
 */

import type { Mode } from './types'

const PREF_KEY = 'continuum.autopilot'

/**
 * Lumen's own lines, in each mode's voice: in Work it is a professional
 * assistant — brief and courteous; in Personal it is relaxed, like a friend.
 * What it does is the same in both; only how it says so changes.
 */
export const LUMEN_LINES: Record<Mode, { pause: string; resume: string; goodbye: string }> = {
  workspace: {
    pause: 'Of course. I’ll wait — say “Lumen” or “I’m back” when you’re ready.',
    resume: 'Welcome back. Go ahead.',
    goodbye: "You're welcome. Talk soon.",
  },
  personal: {
    pause: 'Sure, take your time. Just say “Lumen” when you’re back.',
    resume: 'Hey, welcome back! What’s up?',
    goodbye: 'Anytime! Catch you later.',
  },
}

export const ASSISTANT_NAME = 'Lumen'

const NAME = '(?:lumen|lumin|luman|lumens|loomen|lumon|lumeen)'
const STOP = '(?:stop|sop|wait|hold on|pause|quiet|be quiet|enough|shush)'
const THANKS = '(?:thank you|thanks|thank you very much|thanks a lot|thank you so much)'
/** Ways to say "we're done" — each counts only with Lumen's name attached. */
const CLOSING =
  '(?:' +
  [
    THANKS,
    'bye|bye bye|goodbye|good bye|good night|goodnight',
    'see you|see you later|see ya|talk later|talk to you later|catch you later|later',
    'that s all|that s it|that is all|that is it|that will be all|that ll be all',
    'i m done|i am done|we re done|we are done|all done',
    'stop listening|go to sleep|you can stop now|you can stop|end the session',
  ].join('|') +
  ')'
// "Thanks, that's all, Lumen": two closings, then the name.
const CLOSINGS = `${CLOSING}(?: ${CLOSING})?`
const TAIL = '(?: (?:that s all|that is all|for now|for today|thanks|thank you))?'

const words = (text: string) =>
  text
    .toLowerCase()
    .replace(/[^a-z\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()

// A lone "stop" counts when idle; "sop" only ever next to the name.
const INTERRUPT_ALONE = new RegExp(`^(?:(?:hey|ok|okay) )?(?:${NAME} ${STOP}|${STOP} ${NAME}|stop|wait|hold on|pause)(?: please| now)?$`)
const INTERRUPT_INSIDE = new RegExp(`(?:^| )(?:${NAME} ${STOP}|${STOP} ${NAME})(?: |$)`)
const END_ALONE = new RegExp(
  `^(?:(?:ok|okay|alright|all right) )?(?:${CLOSINGS} ${NAME}|${NAME} ${CLOSINGS})${TAIL}$`,
)
// Without the name: only these, only as the whole of what was said, and only
// when Lumen is idle — never over its own answer, which might say them.
const END_UNNAMED = /^(?:(?:ok|okay|alright|all right) )?(?:goodbye|good bye|bye|bye bye|good night|goodnight|that s all for now|that is all for now)$/
const END_INSIDE = new RegExp(`(?:^| )(?:${CLOSING} ${NAME}|${NAME} (?:${THANKS}|goodbye|good night|stop listening|go to sleep))(?: |$)`)

/** "Give me a minute, Lumen": stay, but stop answering until called back. */
const HOLD = '(?:(?:give me a|one|just a|wait a|hang on a) (?:minute|moment|second|sec)|be right back|brb|wait for me|hold on|hang on|pause|wait|stand by)'
const PAUSE = new RegExp(`^(?:(?:ok|okay) )?(?:${HOLD} ${NAME}|${NAME} ${HOLD})(?: please)?$`)
const BACK = '(?:i m back|i am back|i m here|i am here|back|ready|i m ready|let s continue|lets continue|continue|carry on|resume|we can continue|go on)'
const RESUME = new RegExp(`^(?:(?:hey|hi|ok|okay) )?(?:${NAME}|${BACK}|${BACK} ${NAME}|${NAME} ${BACK})$`)

/** "Stop, Lumen" (or just "stop") said on its own: cut the current answer short. */
export function isInterruptCommand(text: string): boolean {
  return INTERRUPT_ALONE.test(words(text))
}

/**
 * The conversation is over: "thank you, Lumen", "that's all, Lumen", "good
 * night, Lumen", "stop listening, Lumen"… — or, said on its own while Lumen is
 * idle, "goodbye", "bye", "good night" or "that's all for now".
 */
export function isEndCommand(text: string): boolean {
  const said = words(text)
  return END_ALONE.test(said) || END_UNNAMED.test(said)
}

/**
 * "Give me a minute, Lumen" (or "hold on, Lumen" while it is idle): Lumen stays,
 * but takes nothing as a question until it is called back — the room can talk
 * without being answered.
 */
export function isPauseCommand(text: string): boolean {
  return PAUSE.test(words(text))
}

/** While paused: "Lumen", "I'm back", "let's continue, Lumen"… */
export function isResumeCommand(text: string): boolean {
  return RESUME.test(words(text))
}

/**
 * Spoken over Lumen's own answer, a command can come back mixed with its words
 * ("…moved in April stop Lumen it…"), so while it speaks a command is looked for
 * anywhere in what was heard — but only with its name attached, never a bare
 * "stop", which its answer itself might contain.
 */
export function heardInterrupt(text: string): boolean {
  return INTERRUPT_INSIDE.test(words(text))
}

export function heardEnd(text: string): boolean {
  return END_INSIDE.test(words(text))
}

/** Whether Lumen was on when this browser last used Continuum. */
export function readAutopilotPref(): boolean {
  try {
    return localStorage.getItem(PREF_KEY) === '1'
  } catch {
    return false
  }
}

export function writeAutopilotPref(on: boolean): void {
  try {
    localStorage.setItem(PREF_KEY, on ? '1' : '0')
  } catch {
    // Storage blocked: Lumen simply starts off next time.
  }
}

const UNSURE = /(?:^| )(?:not sure|unsure|maybe|don t know|dont know|still deciding|still thinking|i ll think|let me think|not yet)(?: |$)/
const NO = /^(?:no|nope|nah|no thanks|no thank you|not really|i m not)(?: |$)/
const YES = /^(?:yes|yeah|yep|yup|sure|definitely|absolutely|of course|i am|i m going with it|going with it|correct)(?: |$)/

/**
 * The spoken answer to the question Lumen just asked about a pending item —
 * read from the words, right after the question, like its commands. Never a
 * model reading intent (commitment 2). Unsure wins over no, and no over yes:
 * "no, I'm not sure" is not a no, and when in doubt the item stays undecided.
 * Anything else is not an answer: it is the next thing the person wants to say.
 */
export function spokenAnswer(text: string): 'confirm' | 'unsure' | 'decline' | null {
  const said = words(text).replace(new RegExp(`(?:^| )${NAME}(?: |$)`, 'g'), ' ').trim()
  if (!said) return null
  if (UNSURE.test(said)) return 'unsure'
  if (NO.test(said)) return 'decline'
  if (YES.test(said)) return 'confirm'
  return null
}

/** How Lumen puts a pending item to the person. */
export function spokenQuestion(text: string): string {
  return `${text}. Are you going with it? You can say yes, not sure, or no.`
}
