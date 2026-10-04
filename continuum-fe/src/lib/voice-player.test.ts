import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { VoicePlayer } from './voice-player'

/** jsdom cannot play audio: an element that "plays" for one microtask. */
const played: string[] = []
class FakeAudio {
  src = ''
  onended: (() => void) | null = null
  onerror: (() => void) | null = null
  pause() {}
  removeAttribute() {}
  play() {
    played.push(this.src)
    queueMicrotask(() => this.onended?.())
    return Promise.resolve()
  }
}

beforeEach(() => {
  played.length = 0
  vi.stubGlobal('Audio', FakeAudio)
  let n = 0
  // jsdom has no object URLs at all.
  URL.createObjectURL = vi.fn((blob: Blob) => `blob:${blob.size}-${n++}`)
  URL.revokeObjectURL = vi.fn()
})
afterEach(() => {
  vi.unstubAllGlobals()
})

const blob = (text: string) => new Blob([text])

describe('VoicePlayer', () => {
  it('plays in queue order even when a later sentence is synthesised first', async () => {
    const resolvers: Record<string, (b: Blob) => void> = {}
    const synth = vi.fn((text: string) => new Promise<Blob>((r) => (resolvers[text] = r)))
    const player = new VoicePlayer(synth)
    player.enqueue('One.')
    player.enqueue('Two two.')
    player.end()
    expect(synth).toHaveBeenCalledTimes(2) // both fetched at once: no gap between them

    resolvers['Two two.'](blob('22222222'))
    resolvers['One.'](blob('1'))
    await player.idle()
    expect(played.map((src) => src.split('-')[0])).toEqual(['blob:1', 'blob:8'])
  })

  it('falls back to the browser voice when the server cannot speak', async () => {
    const spoken: string[] = []
    vi.stubGlobal('speechSynthesis', { speak: (u: { text: string; onend: () => void }) => { spoken.push(u.text); u.onend() }, cancel() {} })
    vi.stubGlobal('SpeechSynthesisUtterance', class { onend = () => {}; onerror = () => {}; constructor(public text: string) {} })
    const player = new VoicePlayer(() => Promise.reject(new Error('503')))
    player.enqueue('Hello there.')
    player.end()
    await player.idle()
    expect(spoken).toEqual(['Hello there.'])
  })

  it('stop() silences, drops the queue, and releases anyone waiting', async () => {
    const synth = vi.fn(() => new Promise<Blob>(() => {})) // never arrives
    const changes: boolean[] = []
    const player = new VoicePlayer(synth, (speaking) => changes.push(speaking))
    player.enqueue('A sentence.')
    const waiting = player.idle()
    const epoch = player.epoch
    player.stop()
    await waiting
    expect(player.speaking).toBe(false)
    expect(player.epoch).not.toBe(epoch)
    expect(changes.at(-1)).toBe(false)
  })

  it('ignores empty chunks', () => {
    const synth = vi.fn(() => Promise.resolve(blob('x')))
    new VoicePlayer(synth).enqueue('   ')
    expect(synth).not.toHaveBeenCalled()
  })
})
