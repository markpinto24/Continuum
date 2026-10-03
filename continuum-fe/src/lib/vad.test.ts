import { describe, expect, it } from 'vitest'

import { createVad, DEFAULT_VAD, type VadEvent } from './vad'

/** Feed `ms` of a constant level in 50 ms steps; collect the events. */
function feed(vad: ReturnType<typeof createVad>, clock: { now: number }, level: number, ms: number) {
  const events: VadEvent[] = []
  for (let t = 0; t < ms; t += 50) {
    clock.now += 50
    const event = vad.step(level, clock.now)
    if (event) events.push(event)
  }
  return events
}

describe('voice activity detection', () => {
  it('ends an utterance after two seconds of silence, not before', () => {
    const vad = createVad()
    const clock = { now: 0 }
    feed(vad, clock, 0.002, 500) // a quiet room
    expect(feed(vad, clock, 0.1, 1000)).toEqual(['speech-start'])
    expect(feed(vad, clock, 0.002, 1900)).toEqual([]) // still within the pause
    expect(feed(vad, clock, 0.002, 200)).toEqual(['speech-end'])
  })

  it('a pause mid-sentence shorter than two seconds does not end the turn', () => {
    const vad = createVad()
    const clock = { now: 0 }
    feed(vad, clock, 0.002, 300)
    feed(vad, clock, 0.1, 800)
    expect(feed(vad, clock, 0.002, 1500)).toEqual([])
    expect(feed(vad, clock, 0.1, 600)).toEqual([]) // carries on talking
    expect(feed(vad, clock, 0.002, 2100)).toEqual(['speech-end'])
  })

  it('ignores a click too short to be speech', () => {
    const vad = createVad()
    const clock = { now: 0 }
    feed(vad, clock, 0.002, 300)
    expect(feed(vad, clock, 0.1, 50)).toEqual([]) // under the start hold
    expect(feed(vad, clock, 0.002, 2500)).toEqual([])
  })

  it('discards a cough: speech started, but too little voice to be a question', () => {
    const vad = createVad({ ...DEFAULT_VAD, minSpeechMs: 400 })
    const clock = { now: 0 }
    feed(vad, clock, 0.002, 300)
    feed(vad, clock, 0.1, 200)
    expect(feed(vad, clock, 0.002, 2100)).toEqual(['discard'])
  })

  it('raises the bar in a noisy room instead of triggering on it', () => {
    const vad = createVad()
    const clock = { now: 0 }
    expect(feed(vad, clock, 0.012, 5000)).toEqual([]) // a fan, steady, below the start bar
    expect(vad.floor).toBeGreaterThan(0.008)
    expect(feed(vad, clock, 0.02, 600)).toEqual([]) // no longer clearly above the room
    expect(feed(vad, clock, 0.12, 600)).toEqual(['speech-start'])
  })

  it('cuts off at the maximum length', () => {
    const vad = createVad({ ...DEFAULT_VAD, maxSpeechMs: 3000 })
    const clock = { now: 0 }
    feed(vad, clock, 0.002, 200)
    // Still talking past the cap: that utterance is cut, and the voice that
    // carries on is heard as the next one.
    expect(feed(vad, clock, 0.1, 3500).slice(0, 2)).toEqual(['speech-start', 'speech-end'])
  })
})
