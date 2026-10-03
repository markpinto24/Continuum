/**
 * Voice activity detection: when did the speaker start, and when are they done?
 *
 * Fed one loudness reading (RMS, 0..1) every few tens of milliseconds. Pure —
 * no audio APIs — so the rules are testable with synthetic numbers:
 *
 * - Speech starts once the level stays above the start threshold for
 *   `startHoldMs`, so a cough or a door is not a question.
 * - An utterance ends after `silenceMs` of continuous quiet (2 s: long enough for
 *   a mid-sentence pause, short enough not to feel like waiting).
 * - Less than `minSpeechMs` of actual voice is discarded as noise.
 * - Thresholds sit above a noise floor learned while nobody is speaking, so a
 *   fan or a busy room raises the bar instead of triggering it.
 */

export interface VadOptions {
  silenceMs: number
  minSpeechMs: number
  startHoldMs: number
  maxSpeechMs: number
  /** Multiplies both thresholds. Above 1 while Lumen is speaking, so her own
   *  voice coming back through the speakers is less likely to count as yours. */
  sensitivity?: number
  /** Start from a known room level instead of learning it from scratch. */
  initialFloor?: number
}

export type VadEvent = 'speech-start' | 'speech-end' | 'discard' | null

export const DEFAULT_VAD: VadOptions = {
  silenceMs: 2000,
  minSpeechMs: 300,
  startHoldMs: 120,
  maxSpeechMs: 120_000,
}

// Floors for the thresholds: below these, even a silent room's hiss would count.
const MIN_START = 0.015
const MIN_CONTINUE = 0.01

export function createVad(options: VadOptions = DEFAULT_VAD) {
  let floor = options.initialFloor ?? 0.005
  const factor = options.sensitivity ?? 1
  let speaking = false
  let aboveSince: number | null = null
  let startedAt = 0
  let lastVoiceAt = 0
  let voicedMs = 0
  let lastAt: number | null = null

  const thresholds = () => ({
    start: Math.max(MIN_START, floor * 3) * factor,
    keep: Math.max(MIN_CONTINUE, floor * 2) * factor,
  })

  function reset() {
    speaking = false
    aboveSince = null
    voicedMs = 0
  }

  function step(rms: number, now: number): VadEvent {
    const dt = lastAt === null ? 0 : Math.min(now - lastAt, 250)
    lastAt = now
    const { start, keep } = thresholds()

    if (!speaking) {
      // Learn the room only while nobody is talking.
      if (rms < start) floor = floor * 0.97 + rms * 0.03
      if (rms >= start) {
        aboveSince ??= now
        if (now - aboveSince >= options.startHoldMs) {
          speaking = true
          startedAt = aboveSince
          lastVoiceAt = now
          voicedMs = now - aboveSince
          return 'speech-start'
        }
      } else {
        aboveSince = null
      }
      return null
    }

    if (rms >= keep) {
      lastVoiceAt = now
      voicedMs += dt
    }
    const tooLong = now - startedAt >= options.maxSpeechMs
    if (now - lastVoiceAt >= options.silenceMs || tooLong) {
      const enough = voicedMs >= options.minSpeechMs
      reset()
      return enough ? 'speech-end' : 'discard'
    }
    return null
  }

  return {
    step,
    reset,
    get speaking() {
      return speaking
    },
    get floor() {
      return floor
    },
  }
}
