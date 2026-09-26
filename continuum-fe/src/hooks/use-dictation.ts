import { useCallback, useState } from 'react'

import { useRecorder } from '@/hooks/use-recorder'
import { api } from '@/lib/api'

export type DictationState = 'idle' | 'requesting' | 'recording' | 'transcribing'

/**
 * Speak a message instead of typing it: record, send the clip to the server's
 * local Whisper model, hand back the text.
 *
 * The text goes into the chat box, never straight into the conversation —
 * a mis-heard word should be fixed before it is sent, and a sent message is
 * remembered.
 */
export function useDictation({
  maxSeconds,
  onText,
}: {
  maxSeconds: number
  onText: (text: string) => void
}) {
  const [transcribing, setTranscribing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const onRecorded = useCallback(
    async (clip: Blob) => {
      setTranscribing(true)
      try {
        const { text } = await api.transcribe(clip)
        if (text) onText(text)
        else setError('No speech was recognised. Try again, a little closer to the microphone.')
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause))
      } finally {
        setTranscribing(false)
      }
    },
    [onText],
  )

  const recorder = useRecorder({ maxSeconds, onRecorded, onError: setError })

  const start = useCallback(() => {
    setError(null)
    void recorder.start()
  }, [recorder])

  const state: DictationState = transcribing ? 'transcribing' : recorder.state
  return {
    state,
    elapsed: recorder.elapsed,
    error,
    dismissError: () => setError(null),
    start,
    stop: recorder.stop,
    cancel: recorder.cancel,
  }
}
