/**
 * Plays spoken answers: text in, audio out, strictly in order.
 *
 * Each chunk is synthesised by the server's local voice as soon as it is
 * queued, so the next sentence is ready while the current one plays — no gap
 * between sentences, and a streamed answer starts speaking after its first
 * sentence. If the server cannot speak (voice switched off, or an error), the
 * chunk falls back to the browser's own speechSynthesis rather than going
 * silent.
 */

export type Synthesize = (text: string) => Promise<Blob>

interface Item {
  text: string
  audio: Promise<string | null> // object URL, or null to use the browser voice
}

export class VoicePlayer {
  private queue: Item[] = []
  private playing = false
  private finished = false
  private generation = 0
  private element: HTMLAudioElement | null = null
  private idleWaiters: Array<() => void> = []

  constructor(
    private readonly synthesize: Synthesize | null,
    private readonly onChange: (speaking: boolean) => void = () => {},
  ) {}

  /** Changes on every stop(): a stream started before it must stop feeding the queue. */
  get epoch(): number {
    return this.generation
  }

  get speaking(): boolean {
    return this.playing || this.queue.length > 0
  }

  /** Queue one speakable chunk. Synthesis starts now; playback waits its turn. */
  enqueue(text: string): void {
    const clean = text.trim()
    if (!clean) return
    const generation = this.generation
    const audio = this.synthesize
      ? this.synthesize(clean)
          .then((blob) => (generation === this.generation ? URL.createObjectURL(blob) : null))
          .catch(() => null)
      : Promise.resolve(null)
    this.queue.push({ text: clean, audio })
    this.finished = false
    if (!this.playing) void this.drain(generation)
    this.onChange(true)
  }

  /** No more chunks are coming; resolve `idle()` once the queue has played. */
  end(): void {
    this.finished = true
    if (!this.speaking) this.settle()
  }

  /** Resolves when everything queued so far has been spoken, or on stop(). */
  idle(): Promise<void> {
    if (!this.speaking && this.finished) return Promise.resolve()
    return new Promise((resolve) => this.idleWaiters.push(resolve))
  }

  /** Silence now, drop everything queued. */
  stop(): void {
    this.generation += 1
    this.queue = []
    this.playing = false
    this.finished = true
    if (this.element) {
      this.element.pause()
      this.element.removeAttribute('src')
    }
    browserVoice()?.cancel()
    this.settle()
  }

  private settle(): void {
    this.onChange(false)
    const waiters = this.idleWaiters
    this.idleWaiters = []
    waiters.forEach((resolve) => resolve())
  }

  private async drain(generation: number): Promise<void> {
    this.playing = true
    while (generation === this.generation && this.queue.length > 0) {
      const item = this.queue.shift() as Item
      const url = await item.audio
      if (generation !== this.generation) {
        if (url) URL.revokeObjectURL(url)
        return
      }
      if (url) {
        await this.playUrl(url)
        URL.revokeObjectURL(url)
      } else {
        await speakWithBrowser(item.text)
      }
    }
    if (generation !== this.generation) return
    this.playing = false
    if (this.finished) this.settle()
  }

  private playUrl(url: string): Promise<void> {
    // One element, reused: once the user has interacted with the page, the
    // browser lets it play every later chunk without another gesture.
    this.element ??= new Audio()
    const element = this.element
    return new Promise((resolve) => {
      const done = () => {
        element.onended = null
        element.onerror = null
        resolve()
      }
      element.onended = done
      element.onerror = done
      element.src = url
      element.play().catch(done)
    })
  }
}

function browserVoice(): SpeechSynthesis | undefined {
  return typeof window !== 'undefined' ? (window.speechSynthesis ?? undefined) : undefined
}

/** The fallback: the browser's own voice, if it has one. Resolves when done or failed. */
function speakWithBrowser(text: string): Promise<void> {
  const synth = browserVoice()
  if (!synth || typeof window.SpeechSynthesisUtterance !== 'function') return Promise.resolve()
  return new Promise((resolve) => {
    const utterance = new SpeechSynthesisUtterance(text)
    utterance.onend = () => resolve()
    utterance.onerror = () => resolve()
    synth.speak(utterance)
    // Some engines never fire onend when the OS speech service is missing;
    // never let that freeze the queue.
    setTimeout(resolve, 2000 + text.length * 120)
  })
}
