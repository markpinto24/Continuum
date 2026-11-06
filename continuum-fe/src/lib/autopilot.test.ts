import { afterEach, describe, expect, it } from 'vitest'

import {
  heardEnd,
  heardInterrupt,
  isEndCommand,
  isInterruptCommand,
  isPauseCommand,
  isResumeCommand,
  readAutopilotPref,
  spokenAnswer,
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

  it.each([
    "That's all, Lumen.",
    "Thanks, that's all, Lumen",
    'Good night, Lumen.',
    'See you later, Lumen',
    "I'm done, Lumen.",
    "We're done, Lumen",
    'Stop listening, Lumen.',
    'Go to sleep, Lumen',
    'Lumen, goodbye.',
    'Okay, bye Lumen, for now',
    // Said alone, while Lumen is idle.
    'Goodbye.',
    'Bye.',
    'Good night',
    "That's all for now.",
  ])('%j ends the conversation too', (said) => expect(isEndCommand(said)).toBe(true))

  it.each([
    "That's all I need for the gym on Monday",
    "That's it, the second option is better",
    'See you at the meeting tomorrow',
    'I am done with the migration',
    'Good night routine ideas?',
    'Later this week I fly to Pune',
    "That's all",
  ])('%j is a sentence, not a goodbye', (said) => expect(isEndCommand(said)).toBe(false))

  it('over its own answer, a goodbye counts only with its name', () => {
    expect(heardEnd('and that was the plan good night Lumen')).toBe(true)
    expect(heardEnd('so good night and sleep well')).toBe(false)
    expect(heardEnd("that's all for now")).toBe(false)
  })

  it.each(['Give me a minute, Lumen.', 'One moment, Lumen', 'Hold on, Lumen', 'Lumen, be right back', 'Wait for me, Lumen'])(
    '%j pauses',
    (said) => expect(isPauseCommand(said)).toBe(true),
  )

  it.each(['Give me a minute to think about the budget', 'hold on', 'Can you wait until Friday?'])(
    '%j does not pause',
    (said) => expect(isPauseCommand(said)).toBe(false),
  )

  it.each(['Lumen.', "I'm back.", 'Hey Lumen', "Let's continue, Lumen", 'Okay, ready'])('%j resumes', (said) =>
    expect(isResumeCommand(said)).toBe(true),
  )

  it.each(['What did I eat today?', 'Lumen what is my schedule'])('%j does not resume', (said) =>
    expect(isResumeCommand(said)).toBe(false),
  )

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

describe('answering a pending item aloud', () => {
  it.each([
    ['Yes', 'confirm'],
    ["Yeah, I'm going with it, Lumen", 'confirm'],
    ['No thanks', 'decline'],
    ['nope', 'decline'],
    ["I'm not sure yet", 'unsure'],
    ["No, I'm not sure", 'unsure'],
    ['maybe', 'unsure'],
  ] as const)('%s → %s', (said, want) => {
    expect(spokenAnswer(said)).toBe(want)
  })

  it('anything else is the next thing the person wants to say, not an answer', () => {
    expect(spokenAnswer('what should I eat after training?')).toBeNull()
    expect(spokenAnswer('I think the second split is better')).toBeNull()
    expect(spokenAnswer('Lumen')).toBeNull()
  })
})
