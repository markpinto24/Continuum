import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { api } from '@/lib/api'
import { finishedSentences, speechChunks, toSpeakableText } from '@/lib/speakable'
import type { VoiceChoice } from '@/lib/types'
import { VoicePlayer } from '@/lib/voice-player'

export interface SpeechStream {
  /** The whole answer so far; finished sentences are queued as they appear. */
  push: (markdown: string) => void
  /** The answer is complete: queue whatever is left. */
  end: () => void
  /** Resolves once everything has been spoken, or speech was stopped. */
  done: () => Promise<void>
}

/**
 * Speaking answers aloud — one at a time, with the server's local voice and the
 * browser's as a fallback.
 *
 * `speak` reads a finished answer (the Read aloud button); `stream` reads one as
 * it arrives (autopilot), starting after its first sentence.
 */
export function useVoice({
  serverSynthesis,
  voice,
  speed,
}: {
  serverSynthesis: boolean
} & VoiceChoice) {
  const [speakingId, setSpeakingId] = useState<string | null>(null)

  // The chosen voice is read at synthesis time, so changing it in Settings
  // applies to the next sentence without rebuilding the player.
  const choice = useRef<VoiceChoice>({})
  choice.current = { voice, speed }

  const player = useMemo(
    () =>
      new VoicePlayer(
        serverSynthesis ? (text) => api.synthesize(text, choice.current) : null,
        (speaking) => {
          if (!speaking) setSpeakingId(null)
        },
      ),
    [serverSynthesis],
  )
  // Leaving the chat, or swapping players, must not leave it talking.
  useEffect(() => () => player.stop(), [player])
  const playerRef = useRef(player)
  playerRef.current = player

  const supported =
    serverSynthesis || (typeof window !== 'undefined' && 'speechSynthesis' in window)

  const stop = useCallback(() => {
    playerRef.current.stop()
    setSpeakingId(null)
  }, [])

  const speak = useCallback((id: string, markdown: string) => {
    const voice = playerRef.current
    voice.stop()
    const chunks = speechChunks(toSpeakableText(markdown))
    if (chunks.length === 0) return
    setSpeakingId(id)
    chunks.forEach((chunk) => voice.enqueue(chunk))
    voice.end()
  }, [])

  const stream = useCallback((id: string): SpeechStream => {
    const voice = playerRef.current
    voice.stop()
    setSpeakingId(id)
    const epoch = voice.epoch
    // Once speech is stopped (interrupted, or a new answer began), this stream
    // must not keep queueing sentences from an answer still arriving.
    const live = () => voice.epoch === epoch
    let spoken = 0
    let latest = ''
    return {
      push(markdown) {
        if (!live()) return
        latest = markdown
        const sentences = finishedSentences(markdown)
        for (; spoken < sentences.length; spoken++) voice.enqueue(sentences[spoken])
      },
      end() {
        if (!live()) return
        // A trailing space marks the last punctuated sentence as finished; an
        // unpunctuated tail is spoken as it is.
        const sentences = finishedSentences(`${latest} `)
        for (; spoken < sentences.length; spoken++) voice.enqueue(sentences[spoken])
        const tail = toSpeakableText(latest).match(/[^.!?]*$/)?.[0]?.trim()
        if (tail) voice.enqueue(tail)
        voice.end()
      },
      done: () => voice.idle(),
    }
  }, [])

  return { supported, speakingId, speak, stream, stop }
}
