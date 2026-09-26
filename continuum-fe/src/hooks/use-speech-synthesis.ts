import { useCallback, useEffect, useRef, useState } from 'react'

import { speechChunks, toSpeakableText } from '@/lib/speakable'

/**
 * Read text aloud with the browser's own speech synthesis.
 *
 * No server round trip and no audio leaves the machine: the voices are the
 * operating system's (speech-dispatcher on Linux, the system voices on macOS
 * and Windows). One thing speaks at a time — starting another stops the first.
 */
export function useSpeechSynthesis() {
  const supported =
    typeof window !== 'undefined' &&
    'speechSynthesis' in window &&
    typeof window.SpeechSynthesisUtterance === 'function'
  const [speakingId, setSpeakingId] = useState<string | null>(null)
  // Guards against a cancelled run's onend clearing the state of the next one.
  const runRef = useRef(0)

  const stop = useCallback(() => {
    runRef.current += 1
    engine()?.cancel()
    setSpeakingId(null)
  }, [])

  const speak = useCallback(
    (id: string, markdown: string) => {
      const synth = engine()
      if (!synth) return
      synth.cancel()
      const run = ++runRef.current
      const chunks = speechChunks(toSpeakableText(markdown))
      if (chunks.length === 0) return

      const voice = pickVoice(synth.getVoices())
      chunks.forEach((chunk, index) => {
        const utterance = new SpeechSynthesisUtterance(chunk)
        if (voice) utterance.voice = voice
        const last = index === chunks.length - 1
        utterance.onend = () => {
          if (last && runRef.current === run) setSpeakingId(null)
        }
        utterance.onerror = () => {
          if (runRef.current === run) setSpeakingId(null)
        }
        synth.speak(utterance)
      })
      setSpeakingId(id)
    },
    [],
  )

  // Leaving the chat must not leave it talking.
  useEffect(() => () => engine()?.cancel(), [])

  return { supported, speakingId, speak, stop }
}

/** Looked up at each call, not once: the engine can be absent (some Linux
 *  setups, privacy extensions) or go away, and a stale reference would throw. */
function engine(): SpeechSynthesis | undefined {
  return typeof window !== 'undefined' ? window.speechSynthesis ?? undefined : undefined
}

/** The system default for the page's language, else any voice in that language. */
function pickVoice(voices: SpeechSynthesisVoice[]): SpeechSynthesisVoice | undefined {
  const lang = (navigator.language || 'en').toLowerCase()
  const base = lang.split('-')[0]
  const inLang = voices.filter((v) => v.lang.toLowerCase().startsWith(base))
  return (
    inLang.find((v) => v.default) ??
    inLang.find((v) => v.lang.toLowerCase() === lang) ??
    inLang[0] ??
    voices.find((v) => v.default)
  )
}
