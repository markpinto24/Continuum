import { describe, expect, it } from 'vitest'

import { displayName, greeting } from './greeting'

const at = (hour: number) => new Date(2026, 9, 3, hour, 0)

describe('greeting', () => {
  it('greets by time of day and name, and asks what you are working on', () => {
    expect(greeting({ userId: 'mark', openDisputes: 0, now: at(9) })).toBe(
      'Good morning, Mark. What are you working on?',
    )
    expect(greeting({ userId: 'mark', openDisputes: null, now: at(20) })).toMatch(/^Good evening/)
  })

  it('mentions disagreements waiting for a decision', () => {
    expect(greeting({ userId: 'mark', openDisputes: 1, now: at(14) })).toContain(
      'One disagreement is waiting for your decision — ask me about it',
    )
    expect(greeting({ userId: 'mark', openDisputes: 3, now: at(14) })).toContain(
      '3 disagreements are waiting',
    )
  })

  it('leaves out a name it would only mangle', () => {
    expect(displayName('5f3c9a1e0b7d4e2a9c8b1d0e6f4a3b2c')).toBeNull()
    expect(greeting({ userId: 'a1b2c3', openDisputes: 0, now: at(9) })).toBe(
      'Good morning. What are you working on?',
    )
  })
})
