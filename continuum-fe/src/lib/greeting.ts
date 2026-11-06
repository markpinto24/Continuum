/**
 * The line that opens a session — written in chat, or spoken in autopilot.
 *
 * Built locally, not by the model: a greeting should be instant, and it must
 * not invent anything. The one fact it adds is real and useful — how many
 * disagreements are waiting for a decision.
 */

import type { Mode } from './types'

/** A user id that reads as a name ("mark"), not a random account id. */
export function displayName(userId: string): string | null {
  if (!/^[a-z][a-z'-]{1,30}$/i.test(userId)) return null
  return userId.charAt(0).toUpperCase() + userId.slice(1).toLowerCase()
}

export function timeOfDay(now: Date): string {
  const hour = now.getHours()
  if (hour < 12) return 'Good morning'
  if (hour < 18) return 'Good afternoon'
  return 'Good evening'
}

export function greeting({
  userId,
  openDisputes,
  now = new Date(),
  speaker,
  mode = 'workspace',
}: {
  userId: string
  openDisputes: number | null
  now?: Date
  /** Personal asks about your day; Work about what you are working on. */
  mode?: Mode
  /** Spoken by Lumen: she says who she is. */
  speaker?: string
}): string {
  const name = displayName(userId)
  const personal = mode === 'personal'
  // Work is a professional assistant; Personal is relaxed, like a friend.
  const opening = personal
    ? `Hey${name ? ` ${name}` : ''}!`
    : `${timeOfDay(now)}${name ? `, ${name}` : ''}.`
  const hello = `${opening}${speaker ? ` ${speaker} here.` : ''}`
  const one = openDisputes === 1
  const it = one ? 'it' : 'them'
  // Lumen can settle them aloud, and says how; the chat points to the inbox.
  const how = speaker
    ? `say “let’s settle ${it}” and we’ll go through ${it} together, or use the inbox.`
    : personal
      ? `ask me about ${it}, or sort ${it} out in the inbox.`
      : `ask me about ${it}, or settle ${it} in the inbox.`
  const waiting = personal
    ? `${one ? 'One thing you told me doesn’t quite match' : `${openDisputes} things you told me don’t quite match`} — ${how}`
    : `${one ? 'One disagreement is' : `${openDisputes} disagreements are`} waiting for your decision — ${how}`
  const disputes = openDisputes && openDisputes > 0 ? ` ${waiting}` : ''
  return `${hello}${disputes} ${personal ? 'What’s on your mind?' : 'What are you working on?'}`
}
