import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPanel } from '@/components/chat-panel'
import { TooltipProvider } from '@/components/ui/tooltip'

/**
 * Lumen end to end, against a simulated microphone and a fake clock: greeting
 * spoken -> speech -> two seconds of silence -> transcript -> answer spoken
 * sentence by sentence -> commands heard WHILE it speaks -> transcript in chat.
 */

const ME = { user_id: 'mark', email: 'mark@example.com', is_admin: true, via: 'session' as const }
const VOICE = { voice: 'en_GB-alan-medium', speed: 1.25, default_voice: 'en_US-lessac-medium', voices: [] }

const api = vi.hoisted(() => ({
  transcribe: vi.fn(),
  synthesize: vi.fn(),
  streamChat: vi.fn(),
}))

vi.mock('@/lib/api', () => ({
  api: {
    speechStatus: () =>
      Promise.resolve({
        enabled: true, ready: true, max_seconds: 120, language: 'en',
        synthesis: true, synthesis_ready: true, synthesis_max_chars: 800,
      }),
    transcribe: api.transcribe,
    synthesize: api.synthesize,
  },
  streamChat: api.streamChat,
}))

// --- A microphone whose loudness the test controls -------------------------------

let level = 0.002
class FakeAnalyser {
  fftSize = 1024
  getFloatTimeDomainData(samples: Float32Array) {
    samples.fill(level) // RMS of a constant signal is the constant
  }
}
class FakeAudioContext {
  createAnalyser() {
    return new FakeAnalyser()
  }
  createMediaStreamSource() {
    return { connect() {} }
  }
  resume() {
    return Promise.resolve()
  }
  close() {
    return Promise.resolve()
  }
}
class FakeRecorder {
  static isTypeSupported = () => true
  state: 'inactive' | 'recording' = 'inactive'
  mimeType = 'audio/webm;codecs=opus'
  ondataavailable: ((event: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
  start() {
    this.state = 'recording'
  }
  stop() {
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob(['opus']) })
    this.onstop?.()
  }
}

/** Audio that "plays" until the test lets it finish — or until it is paused. */
let holdAudio = false
const playing: FakeAudio[] = []
class FakeAudio {
  src = ''
  onended: (() => void) | null = null
  onerror: (() => void) | null = null
  pause() {
    this.onended?.()
  }
  removeAttribute() {}
  play() {
    if (holdAudio) playing.push(this)
    else queueMicrotask(() => this.onended?.())
    return Promise.resolve()
  }
}
const track = { stop: vi.fn() }

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false })
  level = 0.002
  holdAudio = false
  playing.length = 0
  vi.stubGlobal('AudioContext', FakeAudioContext)
  vi.stubGlobal('MediaRecorder', FakeRecorder)
  vi.stubGlobal('Audio', FakeAudio)
  URL.createObjectURL = vi.fn(() => 'blob:audio')
  URL.revokeObjectURL = vi.fn()
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [track] }) },
  })
  localStorage.clear()
  api.synthesize.mockReset().mockResolvedValue(new Blob(['wav']))
  api.transcribe.mockReset()
  api.streamChat.mockReset().mockImplementation(async (_body, handlers) => {
    handlers.onDelta?.('Atlas runs on Mongo. ')
    handlers.onDelta?.('It moved in April.')
    handlers.onDone?.({ memory_ids: [], cited_ids: [], disagreements: 0, remembered: null })
  })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

async function startLumen() {
  render(
    <TooltipProvider>
      <ChatPanel me={ME} openDisputes={0} voiceSettings={VOICE} onGraphChanged={() => {}} onSelectMemory={() => {}} />
    </TooltipProvider>,
  )
  await advance(10)
  await act(async () => {
    screen.getByRole('button', { name: 'Talk to Lumen' }).click()
  })
  await advance(100)
}

async function say(seconds: number) {
  level = 0.1
  await advance(seconds * 1000)
  level = 0.002
}

const overlay = () => screen.queryByRole('dialog', { name: 'Lumen' })

describe('Lumen', () => {
  it('greets aloud, answers aloud after two seconds of silence, and leaves a transcript', async () => {
    api.transcribe.mockResolvedValue({ text: 'What database are we on?', language: 'en', duration_seconds: 1 })
    await startLumen()

    // The session greeting, in the voice chosen in Settings, before the first listen.
    // Spoken a sentence at a time, so the greeting arrives as two requests.
    const choice = { voice: 'en_GB-alan-medium', speed: 1.25 }
    expect(api.synthesize).toHaveBeenCalledWith(
      expect.stringMatching(/^Good (morning|afternoon|evening), Mark\.$/),
      choice,
    )
    expect(api.synthesize).toHaveBeenCalledWith('Lumen here.', choice)
    expect(screen.getByText('Lumen is listening')).toBeDefined()

    await say(1)
    await advance(1500)
    expect(api.transcribe).not.toHaveBeenCalled() // still inside the two-second pause
    await advance(700)
    expect(api.transcribe).toHaveBeenCalledTimes(1)
    await advance(100)

    expect(api.synthesize).toHaveBeenCalledWith('Atlas runs on Mongo.', expect.anything())
    expect(api.synthesize).toHaveBeenCalledWith('It moved in April.', expect.anything())
    await advance(100)
    expect(screen.getByText('Lumen is listening')).toBeDefined() // and it listens again

    await act(async () => {
      screen.getByRole('button', { name: 'Thank you, Lumen' }).click()
    })
    expect(overlay()).toBeNull()
    expect(screen.getByText('What database are we on?')).toBeDefined()
    expect(screen.getByLabelText('Spoken')).toBeDefined()
    expect(screen.getByText(/Atlas runs on Mongo\. It moved in April\./)).toBeDefined()
    expect(track.stop).toHaveBeenCalled() // microphone released
  })

  it('"stop, Lumen" while it is speaking cuts the answer short and it listens again', async () => {
    api.transcribe
      .mockResolvedValueOnce({ text: 'What database are we on?', language: 'en', duration_seconds: 1 })
      .mockResolvedValueOnce({ text: 'Stop, Lumen.', language: 'en', duration_seconds: 1 })
    await startLumen()
    holdAudio = true // from here, its answer keeps playing until interrupted

    await say(1)
    await advance(2200)
    expect(screen.getByText('Lumen is speaking')).toBeDefined()

    await say(0.6) // spoken over its answer
    await advance(900) // a command needs only 0.7 s of quiet
    expect(api.transcribe).toHaveBeenCalledTimes(2)
    await advance(100)
    expect(screen.getByText('Lumen is listening')).toBeDefined()
    expect(overlay()).not.toBeNull() // interrupted, not ended
  })

  it('"thank you, Lumen" while it is speaking ends the session with a goodbye', async () => {
    api.transcribe
      .mockResolvedValueOnce({ text: 'What database are we on?', language: 'en', duration_seconds: 1 })
      .mockResolvedValueOnce({ text: 'It moved in April thank you Lumen', language: 'en', duration_seconds: 1 })
    await startLumen()
    holdAudio = true

    await say(1)
    await advance(2200)
    await say(0.6)
    await advance(1000)

    expect(overlay()).toBeNull()
    expect(api.synthesize).toHaveBeenCalledWith("You're welcome.", expect.anything())
    expect(api.synthesize).toHaveBeenCalledWith('Talk soon.', expect.anything())
  })

  it('its own words heard back are never taken as a command', async () => {
    api.streamChat.mockImplementation(async (_body, handlers) => {
      handlers.onDelta?.('Mark asked me to stop, Lumen said. More to come. ')
      handlers.onDone?.({ memory_ids: [], cited_ids: [], disagreements: 0, remembered: null })
    })
    api.transcribe
      .mockResolvedValueOnce({ text: 'Tell me a story', language: 'en', duration_seconds: 1 })
      .mockResolvedValueOnce({ text: 'asked me to stop Lumen said', language: 'en', duration_seconds: 1 })
    await startLumen()
    holdAudio = true

    await say(1)
    await advance(2200)
    await say(0.6) // the speakers, picked up by the microphone
    await advance(1000)

    expect(screen.getByText('Lumen is speaking')).toBeDefined() // it carries on
  })

  it('"thank you, Lumen" when idle ends it without asking the model anything', async () => {
    api.transcribe.mockResolvedValue({ text: 'Thank you, Lumen.', language: 'en', duration_seconds: 1 })
    await startLumen()
    await say(1)
    await advance(2300)

    expect(overlay()).toBeNull()
    expect(api.streamChat).not.toHaveBeenCalled()
  })

  it('a noise too short to be speech is ignored', async () => {
    await startLumen()
    await say(0.05)
    await advance(3000)
    expect(api.transcribe).not.toHaveBeenCalled()
  })

  it('remembers it was on, and asks for a tap before it may listen or speak', async () => {
    localStorage.setItem('continuum.autopilot', '1')
    render(
      <TooltipProvider>
        <ChatPanel me={ME} openDisputes={0} onGraphChanged={() => {}} onSelectMemory={() => {}} />
      </TooltipProvider>,
    )
    await advance(10)
    expect(screen.getByText('Lumen was on last time')).toBeDefined()
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled()
    expect(api.synthesize).not.toHaveBeenCalled()
  })
})
