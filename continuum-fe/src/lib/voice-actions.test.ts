import { describe, expect, it } from 'vitest'

import { greeting } from './greeting'
import type { ConflictPair, Memory } from './types'
import {
  consent,
  describeDispute,
  disputeChoice,
  eligible,
  forgetConsent,
  pickOne,
  readBack,
  voiceCommand,
  wantsNext,
} from './voice-actions'

function memory(content: string, extra: Partial<Memory> = {}): Memory {
  return {
    id: content,
    user_id: 'mark',
    content,
    category: 'fact',
    subject: null,
    confidence: 0.8,
    status: 'active',
    redacted_at: null,
    ...extra,
  } as Memory
}

describe('what Lumen acts on', () => {
  it.each([
    ['What are the two disagreements about?', { kind: 'disputes' }],
    ["Let's go through my disputes, Lumen.", { kind: 'disputes' }],
    ['Do I have any conflicts?', { kind: 'disputes' }],
    ["Let's settle them.", { kind: 'disputes' }],
    ['Forget that I am 22.', { kind: 'forget', target: 'i am 22' }],
    ['Lumen, forget my old address.', { kind: 'forget', target: 'my old address' }],
    ['Forget that.', { kind: 'forget', target: null }],
    ["That's still true.", { kind: 'still_true', target: null }],
    ['My partner is Priya is still true', { kind: 'still_true', target: 'my partner is priya' }],
    ["It's still true that I live in Pune", { kind: 'still_true', target: 'i live in pune' }],
    ["That isn't a real fact.", { kind: 'not_a_fact', target: null }],
    ['I never said I like jazz', { kind: 'not_a_fact', target: 'i like jazz' }],
  ])('%s', (said, command) => {
    expect(voiceCommand(said)).toEqual(command)
  })

  it.each([
    'I always forget my keys.',
    'Forget it.',
    'Oh, forget about it.',
    'I had a dispute with my landlord.',
    'Is the gym still open on Sundays?',
    'What did I decide about the launch?',
    '',
  ])('leaves "%s" to the chat', (said) => {
    expect(voiceCommand(said)).toBeNull()
  })

  it("Lumen's greeting tells you a phrase it then understands", () => {
    const line = greeting({ userId: 'mark', openDisputes: 2, speaker: 'Lumen' })
    const phrase = line.match(/“([^”]+)”/)?.[1] ?? ''
    expect(voiceCommand(phrase)).toEqual({ kind: 'disputes' })
  })
})

describe('which side holds', () => {
  it.each([
    ['The first one.', 'first'],
    ['first', 'first'],
    ['The second, Lumen', 'second'],
    ['the latter', 'second'],
    ['Both are true.', 'both'],
    ['keep both', 'both'],
    ['Skip this one', 'skip'],
    ['not now', 'skip'],
    ['mine', 'first'],
    ["the team's", 'second'],
  ])('%s -> %s', (said, choice) => {
    expect(disputeChoice(said)).toBe(choice)
  })

  it.each(['The first — no, the second.', 'both, or the first', 'pizza', ''])(
    'names no single answer in "%s", so Lumen asks again',
    (said) => {
      expect(disputeChoice(said)).toBeNull()
    },
  )
})

describe('agreeing to what Lumen read back', () => {
  it.each(['Yes.', 'yeah, do it', 'Go ahead, Lumen', 'okay'])('%s is a yes', (said) => {
    expect(consent(said)).toBe('yes')
  })
  it.each(['No.', "don't", 'wait', 'cancel that', 'not sure'])('%s is a no', (said) => {
    expect(consent(said)).toBe('no')
  })
  it.each(['yes but the other one', 'hmm', 'what did you say', ''])(
    '"%s" is no answer, and nothing is changed',
    (said) => {
      expect(consent(said)).toBeNull()
    },
  )

  it('forgetting takes "yes, forget it" — never a bare yes, nor "forget it"', () => {
    expect(forgetConsent('Yes, forget it.')).toBe('yes')
    expect(forgetConsent('yeah forget that')).toBe('yes')
    expect(forgetConsent('Yes.')).toBeNull()
    expect(forgetConsent('Forget it.')).toBeNull()
    expect(forgetConsent('no')).toBe('no')
  })

  it('picks one of a list read out, or none', () => {
    expect(pickOne('the second one', 3)).toBe(1)
    expect(pickOne('third', 3)).toBe(2)
    expect(pickOne('third', 2)).toBeNull()
    expect(pickOne('the last one', 2)).toBe(1)
    expect(pickOne('none of them', 3)).toBe('none')
    expect(pickOne('first or second', 3)).toBeNull()
  })

  it('hears "next"', () => {
    expect(wantsNext('Next.')).toBe(true)
    expect(wantsNext('What is my schedule?')).toBe(false)
  })
})

describe('what Lumen says', () => {
  const pair: ConflictPair = {
    memory: memory('Launch is on Friday', { subject: 'launch' }),
    conflicting: [memory('Launch is on Monday', { subject: 'launch' })],
    team: false,
  }

  it('reads both sides in the order the inbox lists them, and asks', () => {
    const text = describeDispute(pair, 1, 2)
    expect(text).toMatch(/Disagreement 1 of 2, about launch/)
    expect(text.indexOf('Friday')).toBeLessThan(text.indexOf('Monday'))
    expect(text).toMatch(/the first, the second, or both/)
  })

  it('reads the decision back, naming what holds and what is kept, then asks', () => {
    expect(readBack(pair, 'second')).toMatch(/“Launch is on Monday” holds, and “Launch is on Friday” is kept as history/)
    expect(readBack(pair, 'both')).toMatch(/both stay/)
    for (const choice of ['first', 'second', 'both'] as const) {
      expect(readBack(pair, choice)).toMatch(/Shall I settle it\? Yes or no\.$/)
    }
  })

  it("says what sharing means when yours holds against the team's", () => {
    expect(readBack({ ...pair, team: true }, 'first')).toMatch(/shared with the team/)
  })

  it('acts only on memories it may: never the team’s, never one already forgotten', () => {
    expect(eligible('forget', memory('x', { user_id: '_shared' }))).toBe(false)
    expect(eligible('forget', memory('x', { redacted_at: '2026-11-01T00:00:00Z' }))).toBe(false)
    expect(eligible('still_true', memory('x', { status: 'pending' }))).toBe(false)
    expect(eligible('not_a_fact', memory('x', { status: 'pending' }))).toBe(true)
    expect(eligible('still_true', memory('x', { status: 'superseded' }))).toBe(false)
  })
})
