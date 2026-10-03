/**
 * Lumen — the hands-free voice mode — in small, pure pieces, kept out of the
 * component so they are testable.
 *
 * Commands are matched against Whisper's transcript, so they tolerate how it
 * spells the name: tested across four voices, "Lumen" came back right 11 times
 * in 12 and once as "Lumin". (Lumen was chosen because no common word sounds
 * like it — "Avery" would have collided with "every".)
 */

const PREF_KEY = 'continuum.autopilot'

export const ASSISTANT_NAME = 'Lumen'

const NAME = '(?:lumen|lumin|luman|lumens|loomen|lumon|lumeen)'
const STOP = '(?:stop|sop|wait|hold on|pause|quiet|be quiet|enough|shush)'
const THANKS = '(?:thank you|thanks|thank you very much|thanks a lot|thank you so much|bye|goodbye|good bye)'

const words = (text: string) =>
  text
    .toLowerCase()
    .replace(/[^a-z\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()

// A lone "stop" counts when idle; "sop" only ever next to the name.
const INTERRUPT_ALONE = new RegExp(`^(?:(?:hey|ok|okay) )?(?:${NAME} ${STOP}|${STOP} ${NAME}|stop|wait|hold on|pause)(?: please| now)?$`)
const INTERRUPT_INSIDE = new RegExp(`(?:^| )(?:${NAME} ${STOP}|${STOP} ${NAME})(?: |$)`)
const END_ALONE = new RegExp(`^(?:(?:ok|okay) )?${THANKS} ${NAME}(?: (?:that s all|that is all|for now))?$`)
const END_INSIDE = new RegExp(`(?:^| )${THANKS} ${NAME}(?: |$)`)

/** "Stop, Lumen" (or just "stop") said on its own: cut the current answer short. */
export function isInterruptCommand(text: string): boolean {
  return INTERRUPT_ALONE.test(words(text))
}

/** "Thank you, Lumen" said on its own: the conversation is over. */
export function isEndCommand(text: string): boolean {
  return END_ALONE.test(words(text))
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
