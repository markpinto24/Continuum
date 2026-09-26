import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPanel } from '@/components/chat-panel'
import { TooltipProvider } from '@/components/ui/tooltip'
import { describeMicError } from '@/hooks/use-recorder'

/**
 * Dictation and read-aloud, against fake browser APIs: jsdom has no
 * microphone, MediaRecorder or speech engine.
 */

const api = vi.hoisted(() => ({
  transcribe: vi.fn(),
  streamChat: vi.fn(),
}))

vi.mock('@/lib/api', () => ({
  api: {
    speechStatus: () =>
      Promise.resolve({ enabled: true, ready: true, max_seconds: 3, language: 'en' }),
    transcribe: api.transcribe,
  },
  streamChat: api.streamChat,
}))

class FakeRecorder {
  static isTypeSupported = (type: string) => type === 'audio/webm;codecs=opus'
  static last: FakeRecorder | null = null
  state: 'inactive' | 'recording' = 'inactive'
  mimeType: string
  ondataavailable: ((event: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
  constructor(_stream: MediaStream, options?: { mimeType?: string }) {
    this.mimeType = options?.mimeType ?? ''
    FakeRecorder.last = this
  }
  start() {
    this.state = 'recording'
  }
  stop() {
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob(['fake-opus'], { type: this.mimeType }) })
    this.onstop?.()
  }
}

const track = { stop: vi.fn() }
const getUserMedia = vi.fn()

beforeEach(() => {
  vi.stubGlobal('MediaRecorder', FakeRecorder)
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia },
  })
  getUserMedia.mockResolvedValue({ getTracks: () => [track] })
  track.stop.mockClear()
  api.transcribe.mockReset()
  api.streamChat.mockReset()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

function renderPanel() {
  return render(
    <TooltipProvider>
      <ChatPanel onGraphChanged={() => {}} onSelectMemory={() => {}} />
    </TooltipProvider>,
  )
}

describe('dictation', () => {
  it('puts the transcript in the box for review — it does not send it', async () => {
    api.transcribe.mockResolvedValue({ text: 'Atlas moved to Mongo.', language: 'en', duration_seconds: 2 })
    renderPanel()

    await userEvent.click(screen.getByRole('button', { name: 'Dictate a message' }))
    expect(await screen.findByText('Listening…')).toBeDefined()
    await userEvent.click(screen.getByRole('button', { name: 'Done' }))

    await waitFor(() =>
      expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('Atlas moved to Mongo.'),
    )
    const clip = api.transcribe.mock.calls[0][0] as Blob
    expect(clip.type).toBe('audio/webm;codecs=opus')
    expect(track.stop).toHaveBeenCalled() // the microphone is released
    expect(api.streamChat).not.toHaveBeenCalled()
  })

  it('appends to what was already typed', async () => {
    api.transcribe.mockResolvedValue({ text: 'on Friday.', language: 'en', duration_seconds: 1 })
    renderPanel()
    await userEvent.type(screen.getByRole('textbox'), 'Ship it ')

    await userEvent.click(screen.getByRole('button', { name: 'Dictate a message' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Done' }))

    await waitFor(() =>
      expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('Ship it on Friday.'),
    )
  })

  it('cancel throws the recording away and frees the microphone', async () => {
    renderPanel()
    await userEvent.click(screen.getByRole('button', { name: 'Dictate a message' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Cancel' }))

    expect(api.transcribe).not.toHaveBeenCalled()
    expect(track.stop).toHaveBeenCalled()
    expect(screen.queryByText('Listening…')).toBeNull()
  })

  it('explains a blocked microphone instead of failing silently', async () => {
    getUserMedia.mockRejectedValue(new DOMException('denied', 'NotAllowedError'))
    renderPanel()
    await userEvent.click(screen.getByRole('button', { name: 'Dictate a message' }))

    expect((await screen.findByRole('alert')).textContent).toContain('Microphone access was blocked')
  })

  it('stops by itself at the length limit', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    api.transcribe.mockResolvedValue({ text: 'long one', language: 'en', duration_seconds: 3 })
    renderPanel()
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    await user.click(await screen.findByRole('button', { name: 'Dictate a message' }))
    await screen.findByText('Listening…')

    await act(async () => {
      vi.advanceTimersByTime(3500) // max_seconds is 3 in the mocked status
    })

    await waitFor(() => expect(api.transcribe).toHaveBeenCalledTimes(1))
  })

  it('says so when nothing intelligible was heard', async () => {
    api.transcribe.mockResolvedValue({ text: '', language: 'en', duration_seconds: 1 })
    renderPanel()
    await userEvent.click(screen.getByRole('button', { name: 'Dictate a message' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Done' }))

    expect((await screen.findByRole('alert')).textContent).toContain('No speech was recognised')
  })
})

describe('describeMicError', () => {
  it.each([
    ['NotAllowedError', 'blocked'],
    ['NotFoundError', 'No microphone'],
    ['NotReadableError', 'in use'],
  ])('%s', (name, phrase) => {
    expect(describeMicError(new DOMException('x', name))).toContain(phrase)
  })
})

describe('read aloud', () => {
  it('speaks the answer without its markup, and stops on request', async () => {
    const spoken: string[] = []
    const synth = {
      speak: vi.fn((u: { text: string }) => spoken.push(u.text)),
      cancel: vi.fn(),
      getVoices: () => [],
    }
    vi.stubGlobal('speechSynthesis', synth)
    vi.stubGlobal(
      'SpeechSynthesisUtterance',
      class {
        text: string
        onend: (() => void) | null = null
        onerror: (() => void) | null = null
        constructor(text: string) {
          this.text = text
        }
      },
    )
    api.streamChat.mockImplementation(async (_body, handlers) => {
      handlers.onDelta?.('**Mark** is the admin [1].')
      handlers.onDone?.({ memory_ids: [], cited_ids: [], disagreements: 0, remembered: null })
    })

    renderPanel()
    await userEvent.type(screen.getByRole('textbox'), 'who is admin?{Enter}')

    await userEvent.click(await screen.findByRole('button', { name: 'Read aloud' }))
    expect(spoken).toEqual(['Mark is the admin.'])

    await userEvent.click(screen.getByRole('button', { name: 'Stop reading aloud' }))
    expect(synth.cancel).toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Read aloud' })).toBeDefined()
  })
})
