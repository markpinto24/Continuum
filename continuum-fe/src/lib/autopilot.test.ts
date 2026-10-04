import { afterEach, describe, expect, it } from 'vitest'

import {
  heardEnd,
  heardInterrupt,
  isEndCommand,
  isInterruptCommand,
  readAutopilotPref,
  writeAutopilotPref,
} from './autopilot'

describe("Lumen's commands", () => {
  it.each(['Stop, Lumen.', 'Lumen, stop!', 'stop', 'Hold on, Lumen.', 'Okay Lumin stop please', 'Sop, Lumen.'])(
    '%j interrupts',
    (said) => expect(isInterruptCommand(said)).toBe(true),
  )

  it.each(['Thank you, Lumen.', 'Thanks Lumen', 'Thank you so much, Lumin.', 'Goodbye, Lumen. That is all.'])(
    '%j ends the conversation',
    (said) => expect(isEndCommand(said)).toBe(true),
  )

  it.each([
    'Should we stop the Atlas migration?',
    'What did Lumen say about the budget?',
    'sop',
    'Thank you for the summary',
    'We stopped using Postgres',
  ])('%j is a question, not a command', (said) => {
    expect(isInterruptCommand(said)).toBe(false)
    expect(isEndCommand(said)).toBe(false)
  })

  it('finds a command spoken over its answer, mixed with its own words', () => {
    expect(heardInterrupt('the event store moved in April stop Lumen it was')).toBe(true)
    expect(heardEnd('and Mongo since then thank you Lumen')).toBe(true)
  })

  it('never takes a bare "stop" from inside its own answer as a command', () => {
    expect(heardInterrupt('we decided to stop the deploy on Fridays')).toBe(false)
  })
})

describe('the remembered preference', () => {
  afterEach(() => localStorage.clear())

  it('remembers whether Lumen was on', () => {
    expect(readAutopilotPref()).toBe(false)
    writeAutopilotPref(true)
    expect(readAutopilotPref()).toBe(true)
  })
})
