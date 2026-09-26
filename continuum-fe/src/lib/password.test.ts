import { describe, expect, it } from 'vitest'

import { newPasswordFeedback } from './password'

describe('newPasswordFeedback', () => {
  it('counts down to the minimum', () => {
    expect(newPasswordFeedback('', '').lengthHint).toBe('At least 10 characters.')
    expect(newPasswordFeedback('abc', '').lengthHint).toBe('At least 10 characters — 7 more.')
    expect(newPasswordFeedback('abcdefghijk', '').lengthHint).toBe('11 characters.')
  })

  it('stays quiet while the confirmation could still match', () => {
    expect(newPasswordFeedback('correct horse', 'correct').mismatch).toBeNull()
  })

  it('flags a confirmation that can no longer match', () => {
    // The case in the screenshot: 11 characters, then 6 that diverge.
    expect(newPasswordFeedback('correct horse', 'wrongg').mismatch).toBe('The passwords do not match.')
    expect(newPasswordFeedback('abc', 'abcd').mismatch).toBe('The passwords do not match.')
  })

  it('is ready only when long enough and matching', () => {
    expect(newPasswordFeedback('short', 'short').ready).toBe(false)
    expect(newPasswordFeedback('long enough pw', 'long enough p').ready).toBe(false)
    expect(newPasswordFeedback('long enough pw', 'long enough pw').ready).toBe(true)
  })
})
