import { useCallback, useEffect, useRef, useState } from 'react'

export type RecorderState = 'idle' | 'requesting' | 'recording'

/** Formats the server's decoder reads, best first. Chromium and Brave record
 *  webm/opus, Firefox ogg/opus, Safari mp4. */
const MIME_CANDIDATES = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/webm', 'audio/mp4']

export function pickMimeType(): string | undefined {
  if (typeof MediaRecorder === 'undefined') return undefined
  return MIME_CANDIDATES.find((type) => MediaRecorder.isTypeSupported(type))
}

/** Why recording could not start, in words a person can act on. */
export function describeMicError(error: unknown): string {
  const name = error instanceof DOMException ? error.name : ''
  if (name === 'NotAllowedError' || name === 'SecurityError')
    return 'Microphone access was blocked. Allow it for this site in the browser, then try again.'
  if (name === 'NotFoundError' || name === 'OverconstrainedError')
    return 'No microphone was found.'
  if (name === 'NotReadableError')
    return 'The microphone is in use by another application.'
  return error instanceof Error ? error.message : 'Could not start recording.'
}

/**
 * Record one clip from the microphone.
 *
 * The microphone is released the moment recording stops — the browser's
 * "recording" indicator goes off with it — and nothing is kept once the clip is
 * handed to `onRecorded`. Recording stops by itself at `maxSeconds`.
 */
export function useRecorder({
  maxSeconds,
  onRecorded,
  onError,
}: {
  maxSeconds: number
  onRecorded: (clip: Blob) => void
  onError: (message: string) => void
}) {
  const [state, setState] = useState<RecorderState>('idle')
  const [elapsed, setElapsed] = useState(0)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const cancelledRef = useRef(false)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  // Callbacks change every render; the recorder's handlers must see the latest.
  const handlers = useRef({ onRecorded, onError })
  handlers.current = { onRecorded, onError }

  const release = useCallback(() => {
    if (timerRef.current) clearInterval(timerRef.current)
    timerRef.current = null
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    recorderRef.current = null
    setState('idle')
  }, [])

  const stop = useCallback(() => {
    const recorder = recorderRef.current
    if (recorder && recorder.state !== 'inactive') recorder.stop()
  }, [])

  const cancel = useCallback(() => {
    cancelledRef.current = true
    stop()
    if (!recorderRef.current) release()
  }, [release, stop])

  const start = useCallback(async () => {
    if (recorderRef.current) return
    if (!navigator.mediaDevices?.getUserMedia) {
      // Browsers only expose the microphone to secure pages.
      handlers.current.onError(
        'Dictation needs a secure page. Open Continuum at http://localhost:5173 or over HTTPS.',
      )
      return
    }
    if (typeof MediaRecorder === 'undefined') {
      handlers.current.onError('This browser cannot record audio.')
      return
    }

    setState('requesting')
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
      })
    } catch (error) {
      setState('idle')
      handlers.current.onError(describeMicError(error))
      return
    }

    const mimeType = pickMimeType()
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
    streamRef.current = stream
    recorderRef.current = recorder
    chunksRef.current = []
    cancelledRef.current = false

    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunksRef.current.push(event.data)
    }
    recorder.onstop = () => {
      const clip = new Blob(chunksRef.current, { type: recorder.mimeType || mimeType || 'audio/webm' })
      chunksRef.current = []
      const cancelled = cancelledRef.current
      release()
      if (!cancelled && clip.size > 0) handlers.current.onRecorded(clip)
    }

    recorder.start()
    setElapsed(0)
    setState('recording')
    const started = Date.now()
    timerRef.current = setInterval(() => {
      const seconds = Math.floor((Date.now() - started) / 1000)
      setElapsed(seconds)
      if (seconds >= maxSeconds) stop()
    }, 250)
  }, [maxSeconds, release, stop])

  // Unmounting mid-recording must not leave the microphone on.
  useEffect(
    () => () => {
      cancelledRef.current = true
      if (recorderRef.current?.state === 'recording') recorderRef.current.stop()
      streamRef.current?.getTracks().forEach((track) => track.stop())
      if (timerRef.current) clearInterval(timerRef.current)
    },
    [],
  )

  return { state, elapsed, start, stop, cancel }
}
