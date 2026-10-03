import { useCallback, useEffect, useRef, useState } from 'react'

import { describeMicError, pickMimeType } from '@/hooks/use-recorder'
import { DEFAULT_VAD, createVad, type VadOptions } from '@/lib/vad'

export type PilotPhase = 'off' | 'starting' | 'listening' | 'hearing' | 'working'

/** A recording waiting for speech is restarted after this long, so the clip
 *  that finally holds a question never carries minutes of silence before it. */
const MAX_PREROLL_MS = 15_000
const TICK_MS = 50

/**
 * While Lumen is thinking or speaking, a second, warier listener runs — for its
 * commands only ("stop, Lumen", "thank you, Lumen"). Its own voice comes back
 * through the speakers, so the bar is raised (it starts from the room level and
 * is 2.5x stricter), and a command is short: 0.7 s of quiet ends it.
 */
const BARGE_VAD: Omit<VadOptions, 'initialFloor'> = {
  silenceMs: 700,
  minSpeechMs: 300,
  startHoldMs: 150,
  maxSpeechMs: 10_000,
  sensitivity: 2.5,
}
const BARGE_PREROLL_MS = 8_000

interface Take {
  recorder: MediaRecorder
  chunks: Blob[]
  since: number
}

function record(stream: MediaStream): Take {
  const mimeType = pickMimeType()
  const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
  const take: Take = { recorder, chunks: [], since: performance.now() }
  recorder.ondataavailable = (event) => {
    if (event.data.size > 0) take.chunks.push(event.data)
  }
  recorder.start()
  return take
}

/** Stop a recording; resolve with its audio, or null if discarded or empty. */
function finish(take: Take | null, keep: boolean): Promise<Blob | null> {
  if (!take || take.recorder.state === 'inactive') return Promise.resolve(null)
  return new Promise((resolve) => {
    take.recorder.onstop = () => {
      const clip = new Blob(take.chunks, { type: take.recorder.mimeType || 'audio/webm' })
      resolve(keep && clip.size > 0 ? clip : null)
    }
    take.recorder.stop()
  })
}

/**
 * Hands-free conversation: listen, notice when the speaker has finished, hand
 * over the recording, wait, listen again.
 *
 * While `onUtterance` runs (transcribing, thinking, speaking the answer) the
 * main listener is paused — Lumen must not answer its own voice. If barge-in is
 * switched on for that stretch (`setBargeIn(true)`), the warier listener above
 * hands short clips to `onBargeIn`, which decides whether they were commands.
 *
 * The live loudness goes into `levelRef` instead of React state: it changes
 * twenty times a second, and the meter reads it directly.
 */
export function useAutopilot({
  silenceMs = DEFAULT_VAD.silenceMs,
  maxSeconds,
  onUtterance,
  onBargeIn,
  onError,
}: {
  silenceMs?: number
  maxSeconds: number
  onUtterance: (clip: Blob) => Promise<void>
  onBargeIn?: (clip: Blob) => Promise<void>
  onError: (message: string) => void
}) {
  const [phase, setPhase] = useState<PilotPhase>('off')
  const levelRef = useRef(0)
  const handlers = useRef({ onUtterance, onBargeIn, onError })
  handlers.current = { onUtterance, onBargeIn, onError }

  const live = useRef<{
    stream: MediaStream
    context: AudioContext
    analyser: AnalyserNode
    samples: Float32Array<ArrayBuffer>
    take: Take | null
    timer: ReturnType<typeof setInterval>
    vad: ReturnType<typeof createVad>
    busy: boolean
    barge: { on: boolean; take: Take | null; vad: ReturnType<typeof createVad>; checking: boolean }
  } | null>(null)

  const listen = useCallback(() => {
    const state = live.current
    if (state) state.take = record(state.stream)
  }, [])

  const stopBarge = useCallback(() => {
    const state = live.current
    if (!state) return
    state.barge.on = false
    void finish(state.barge.take, false)
    state.barge.take = null
  }, [])

  /** Listen for Lumen's commands while it is busy (true), or stop (false). */
  const setBargeIn = useCallback(
    (on: boolean) => {
      const state = live.current
      if (!state || !handlers.current.onBargeIn) return
      if (!on) return stopBarge()
      if (state.barge.on) return
      state.barge.on = true
      state.barge.vad = createVad({ ...BARGE_VAD, initialFloor: state.vad.floor })
      state.barge.take = record(state.stream)
    },
    [stopBarge],
  )

  const stop = useCallback(() => {
    const state = live.current
    live.current = null
    if (state) {
      clearInterval(state.timer)
      for (const take of [state.take, state.barge.take]) {
        if (take && take.recorder.state !== 'inactive') take.recorder.stop()
      }
      state.stream.getTracks().forEach((track) => track.stop())
      void state.context.close()
    }
    levelRef.current = 0
    setPhase('off')
  }, [])

  /** Run `task` with the main listener paused, then go back to listening. */
  const hold = useCallback(
    async (task: () => Promise<void>) => {
      const state = live.current
      if (!state || state.busy) return
      state.busy = true
      await finish(state.take, false)
      state.take = null
      setPhase('working')
      try {
        await task()
      } catch (error) {
        handlers.current.onError(error instanceof Error ? error.message : String(error))
      } finally {
        if (live.current === state) {
          stopBarge()
          state.busy = false
          state.vad.reset()
          listen()
          setPhase('listening')
        }
      }
    },
    [listen, stopBarge],
  )

  const tickBarge = useCallback((rms: number, now: number) => {
    const state = live.current
    const barge = state?.barge
    if (!state || !barge?.on || barge.checking) return
    const event = barge.vad.step(rms, now)
    const restart = () => {
      if (live.current === state && barge.on) {
        barge.vad.reset()
        barge.take = record(state.stream)
      }
    }
    if (event === 'speech-end') {
      barge.checking = true
      const take = barge.take
      barge.take = null
      void finish(take, true).then(async (clip) => {
        try {
          if (clip && live.current === state) await handlers.current.onBargeIn?.(clip)
        } finally {
          barge.checking = false
          restart()
        }
      })
    } else if (event === 'discard') {
      void finish(barge.take, false).then(restart)
      barge.take = null
    } else if (!barge.vad.speaking && barge.take && now - barge.take.since > BARGE_PREROLL_MS) {
      void finish(barge.take, false).then(restart)
      barge.take = null
    }
  }, [])

  const tick = useCallback(() => {
    const state = live.current
    if (!state) return
    state.analyser.getFloatTimeDomainData(state.samples)
    let sum = 0
    for (const sample of state.samples) sum += sample * sample
    const rms = Math.sqrt(sum / state.samples.length)
    levelRef.current = rms
    const now = performance.now()

    if (state.busy) {
      tickBarge(rms, now)
      return
    }

    const event = state.vad.step(rms, now)
    if (event === 'speech-start') setPhase('hearing')
    else if (event === 'discard') {
      void finish(state.take, false).then(() => live.current === state && listen())
      state.take = null
      setPhase('listening')
    } else if (event === 'speech-end') {
      state.busy = true
      setPhase('working')
      const take = state.take
      state.take = null
      void finish(take, true).then((clip) => {
        state.busy = false
        if (live.current !== state) return
        if (clip) void hold(() => handlers.current.onUtterance(clip))
        else listen()
      })
    } else if (!state.vad.speaking && state.take && now - state.take.since > MAX_PREROLL_MS) {
      void finish(state.take, false).then(() => live.current === state && listen())
      state.take = null
    }
  }, [hold, listen, tickBarge])

  const start = useCallback(async (): Promise<boolean> => {
    if (live.current) return true
    if (!navigator.mediaDevices?.getUserMedia) {
      handlers.current.onError(
        'Lumen needs a secure page. Open Continuum at http://localhost:5173 or over HTTPS.',
      )
      return false
    }
    if (typeof MediaRecorder === 'undefined' || typeof AudioContext === 'undefined') {
      handlers.current.onError('This browser cannot record audio.')
      return false
    }
    setPhase('starting')
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        // Echo cancellation also helps Lumen not hear itself while it speaks.
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      })
    } catch (error) {
      setPhase('off')
      handlers.current.onError(describeMicError(error))
      return false
    }
    const context = new AudioContext()
    await context.resume().catch(() => {})
    const analyser = context.createAnalyser()
    analyser.fftSize = 1024
    context.createMediaStreamSource(stream).connect(analyser)

    const vad = createVad({ ...DEFAULT_VAD, silenceMs, maxSpeechMs: maxSeconds * 1000 })
    live.current = {
      stream,
      context,
      analyser,
      samples: new Float32Array(analyser.fftSize),
      take: null,
      timer: setInterval(() => tick(), TICK_MS),
      vad,
      busy: false,
      barge: { on: false, take: null, vad: createVad(BARGE_VAD), checking: false },
    }
    listen()
    setPhase('listening')
    return true
  }, [listen, maxSeconds, silenceMs, tick])

  useEffect(() => stop, [stop])

  return { phase, levelRef, start, stop, hold, setBargeIn, active: phase !== 'off' }
}
