/**
 * The line that opens a session — written in chat, or spoken in autopilot.
 *
 * Built locally, not by the model: a greeting should be instant, and it must
 * not invent anything. The one fact it adds is real and useful — how many
 * disagreements are waiting for a decision.
 */

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
}: {
  userId: string
  openDisputes: number | null
  now?: Date
  /** Spoken by Lumen: she says who she is. */
  speaker?: string
}): string {
  const name = displayName(userId)
  const hello = `${timeOfDay(now)}${name ? `, ${name}` : ''}.${speaker ? ` ${speaker} here.` : ''}`
  const disputes =
    openDisputes && openDisputes > 0
      ? ` ${openDisputes === 1 ? 'One disagreement is' : `${openDisputes} disagreements are`} waiting for your decision — ask me about ${openDisputes === 1 ? 'it' : 'them'}, or settle ${openDisputes === 1 ? 'it' : 'them'} in the inbox.`
      : ''
  return `${hello}${disputes} What are you working on?`
}
