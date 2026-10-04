/**
 * Live feedback for choosing a new password — shared by first-run setup and
 * "change password", so both explain themselves the same way.
 *
 * Mirrors the server's rule (PASSWORD_MIN_LENGTH, default 10). The server still
 * enforces it; this only saves a round trip to learn about it.
 */
export const PASSWORD_MIN_LENGTH = 10

export interface NewPasswordFeedback {
  /** Under the password: how far from the minimum, or nothing once it is met. */
  lengthHint: string
  /** Under the confirmation: set only once it can no longer be a prefix-in-progress. */
  mismatch: string | null
  /** Both rules met, and the confirmation matches. */
  ready: boolean
}

export function newPasswordFeedback(password: string, confirm: string): NewPasswordFeedback {
  const short = PASSWORD_MIN_LENGTH - password.length
  const lengthHint =
    short > 0
      ? `At least ${PASSWORD_MIN_LENGTH} characters${password ? ` — ${short} more` : ''}.`
      : `${password.length} characters.`

  // Quiet while the confirmation is still being typed and could yet match:
  // flagging "c" as wrong for "correct horse" is noise, not help.
  const stillTyping = password.startsWith(confirm) && confirm.length < password.length
  const mismatch =
    confirm && confirm !== password && !stillTyping ? 'The passwords do not match.' : null

  return { lengthHint, mismatch, ready: short <= 0 && confirm === password }
}
