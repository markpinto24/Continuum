import { AudioLines, MicOff, Square, X } from 'lucide-react'
import { type RefObject, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'

import { BlurWords } from '@/components/blur-words'
import { Markdown } from '@/components/markdown'
import Aurora from '@/components/reactbits/Aurora'
import Orb from '@/components/reactbits/Orb'
import ShinyText from '@/components/reactbits/ShinyText'
import type { PilotPhase } from '@/hooks/use-autopilot'
import { useMediaQuery } from '@/hooks/use-media-query'
import { type HudMode, LUMEN_THEMES, inkAt, inkHex } from '@/lib/lumen-theme'
import type { Mode } from '@/lib/types'
import { cn } from '@/lib/utils'

export type PilotStage = 'transcribing' | 'thinking' | 'speaking' | 'greeting' | null

/**
 * Where Lumen is drawn. `overlay` covers the whole page above everything (the
 * chat on its own, as in the tests); `full` fills the app's Lumen screen; and
 * `docked` fills the panel beside the graph when Lumen takes over from the chat.
 */
export type StageLayout = 'overlay' | 'full' | 'docked'

const LABEL: Record<PilotPhase, string> = {
  off: '',
  starting: 'Waking Lumen…',
  listening: 'Lumen is listening',
  hearing: 'Lumen is hearing you…',
  working: '',
}

const STAGE_LABEL: Record<Exclude<PilotStage, null>, string> = {
  greeting: 'Lumen is saying hello…',
  transcribing: 'Lumen is catching what you said…',
  thinking: 'Lumen is thinking…',
  speaking: 'Lumen is speaking',
}

const MODE_TAG: Record<HudMode, string> = {
  idle: 'Standby',
  listening: 'Online',
  hearing: 'Voice detected',
  processing: 'Transcribing',
  thinking: 'Processing',
  speaking: 'Responding',
}

/** Longer than a couple of spoken lines: shown in full beside Lumen. */
const LONG_ANSWER = 220
const REDUCED_MOTION = '(prefers-reduced-motion: reduce)'

export interface LumenStageProps {
  phase: PilotPhase
  stage: PilotStage
  /** What it heard you say last. */
  caption: string | null
  /** What it is saying now, as it is written. */
  answer?: string | null
  levelRef: RefObject<number>
  /** The session is live: it listens, thinks and speaks. */
  active: boolean
  /** "Give me a minute, Lumen": listening only for its name. */
  paused?: boolean
  /** Restored from last time: the browser needs a click before it may speak or listen. */
  needsTap: boolean
  /** Which memory it talks from — and so its colours. */
  mode?: Mode
  layout?: StageLayout
  /** The session's opening line, shown while Lumen waits for a tap. */
  greeting?: string
  /** False when the server offers no voice: there is nothing to start. */
  canTalk?: boolean
  /** Why Lumen could not start (no microphone, an insecure page…). */
  error?: string | null
  /**
   * A long answer gets its own panel beside Lumen, in full, instead of the last
   * lines as a subtitle. Off when the chat is on screen: it shows the transcript.
   */
  answerBeside?: boolean
  /**
   * The chat is on screen beside Lumen and already shows the answer as it
   * streams: Lumen then shows none of it — no subtitle, no card — only what it
   * is doing and what you asked.
   */
  chatOnScreen?: boolean
  onDismissError?: () => void
  /** Start Lumen, or resume it — the click a browser needs first. */
  onTap: () => void
  onInterrupt: () => void
  /** "Thank you, Lumen": it says goodbye and the session ends. */
  onThankYou: () => void
  /** Dismiss the resume prompt without starting. */
  onClose: () => void
}

/**
 * Lumen's screen — the hands-free voice mode, as a heads-up display.
 *
 * Its turns are written into the chat as they happen, so whichever screen shows
 * the chat next has the whole transcript. The motion is driven straight to the
 * DOM from the microphone level (`levelRef`), not through React state — it
 * changes every frame.
 */
export function LumenStage({
  phase,
  stage,
  caption,
  answer,
  levelRef,
  active,
  needsTap,
  paused = false,
  mode: memoryMode = 'workspace',
  layout = 'overlay',
  greeting,
  canTalk = true,
  error = null,
  answerBeside = false,
  chatOnScreen = false,
  onDismissError,
  onTap,
  onInterrupt,
  onThankYou,
  onClose,
}: LumenStageProps) {
  const theme = LUMEN_THEMES[memoryMode]
  const waiting = !active && !needsTap
  // Paused and not saying anything: at rest, listening only for its name.
  const resting = paused && !stage
  const hud: HudMode =
    needsTap || waiting || resting
      ? 'idle'
      : phase === 'hearing'
        ? 'hearing'
        : phase === 'listening' || phase === 'starting'
          ? 'listening'
          : stage === 'transcribing'
            ? 'processing'
            : stage === 'speaking' || stage === 'greeting'
              ? 'speaking'
              : 'thinking'
  const color = theme.colors[hud]
  const ink = theme.ink
  const docked = layout === 'docked'
  const reduced = useMediaQuery(REDUCED_MOTION)

  const label = needsTap
    ? 'Lumen was on last time'
    : resting
      ? 'Paused — say “Lumen” when you’re back'
      : phase === 'working'
        ? (stage && STAGE_LABEL[stage]) || 'Working…'
        : LABEL[phase]
  const busy = stage === 'speaking' || stage === 'thinking'
  // Kept after it has spoken, until the next question, so it can be read.
  const beside =
    !chatOnScreen && answerBeside && active && !!answer && answer.length > LONG_ANSWER

  return (
    <div
      role={layout === 'overlay' ? 'dialog' : 'region'}
      aria-label="Lumen"
      className={cn(
        'flex flex-col overflow-hidden animate-fade-up',
        layout === 'overlay' ? 'fixed inset-0 z-50' : 'absolute inset-0',
        docked && 'rounded-2xl',
      )}
      style={{ ['--hud' as string]: color, background: theme.base, color: ink }}
    >
      <HudBackdrop color={color} aurora={theme.aurora} paused={reduced} docked={docked} />

      <header className={cn('relative flex items-center justify-between', docked ? 'px-4 py-3.5' : 'px-6 py-5')}>
        <div className="flex items-center gap-3">
          <span
            className="text-[13px] font-semibold tracking-[0.35em]"
            style={{ color: inkAt(ink, 90) }}
          >
            LUMEN
          </span>
          <span
            className="flex items-center gap-1.5 rounded-full px-2.5 py-0.5 font-mono text-[10px] tracking-[0.18em] uppercase transition-colors duration-500"
            style={{ color, boxShadow: `inset 0 0 0 1px ${color}55`, background: `${color}12` }}
          >
            <span className="size-1.5 rounded-full hud-breathe" style={{ background: color }} />
            {resting ? 'Paused' : MODE_TAG[hud]}
          </span>
        </div>
        {layout === 'overlay' && (
          <button
            type="button"
            onClick={needsTap ? onClose : onThankYou}
            aria-label="Close Lumen"
            className="rounded-full p-2 transition-colors hover:bg-white/5"
            style={{ color: inkAt(ink, 55) }}
          >
            <X className="size-5" />
          </button>
        )}
      </header>

      <main
        className={cn(
          'relative flex min-h-0 flex-1 items-center justify-center px-6',
          beside && !docked ? 'flex-row gap-10' : 'flex-col',
          docked ? 'gap-5' : 'gap-8',
        )}
      >
        <div className={cn('flex min-w-0 flex-col items-center', docked ? 'gap-5' : 'gap-8')}>
          <Reactor mode={hud} color={color} orb={theme.orb} levelRef={levelRef} compact={docked || beside} reduced={reduced} />

          <div className="flex max-w-xl flex-col items-center gap-3 text-center" aria-live="polite">
            {waiting ? (
              <Waiting greeting={greeting} canTalk={canTalk} ink={ink} color={color} docked={docked} />
            ) : (
              <>
                <p className="text-lg font-light tracking-wide transition-colors" style={{ color: inkAt(ink, 95) }}>
                  {label}
                </p>
                {!needsTap && caption && (
                  <p className="max-w-md text-sm leading-relaxed" style={{ color: inkAt(ink, 55) }}>
                    <span
                      className="mr-2 font-mono text-[10px] tracking-[0.2em] uppercase"
                      style={{ color: inkAt(ink, 40) }}
                    >
                      You
                    </span>
                    “{caption}”
                  </p>
                )}
              </>
            )}
            {error && (
              <div
                role="alert"
                className="flex max-w-md items-start gap-2 rounded-xl px-3.5 py-2.5 text-left text-xs leading-relaxed"
                style={{ background: '#f9731618', boxShadow: 'inset 0 0 0 1px #f9731640', color: '#fed7aa' }}
              >
                <MicOff className="mt-px size-3.5 shrink-0" />
                <span className="flex-1">{error}</span>
                {onDismissError && (
                  <button type="button" aria-label="Dismiss" onClick={onDismissError} className="opacity-70 hover:opacity-100">
                    <X className="size-3.5" />
                  </button>
                )}
              </div>
            )}
          </div>

          {!chatOnScreen && !beside && active && answer && (stage === 'speaking' || stage === 'thinking') && (
            <Subtitle text={answer} color={color} ink={ink} />
          )}
        </div>

        {beside && answer && <AnswerCard text={answer} color={color} ink={ink} docked={docked} />}
      </main>

      <footer
        className={cn(
          'relative flex flex-col items-center gap-4 px-6',
          // The app's dock floats over the bottom of the full screen.
          docked ? 'pb-5' : 'pb-8',
        )}
      >
        {active && (
          <p className="text-center font-mono text-[11px] tracking-wide" style={{ color: inkAt(ink, 40) }}>
            {busy ? (
              <>
                Say <Kbd ink={ink}>stop, Lumen</Kbd> to interrupt
              </>
            ) : (
              <>
                Speak, then pause for two seconds · say <Kbd ink={ink}>thank you, Lumen</Kbd> when you
                are done
              </>
            )}
          </p>
        )}
        <div className="flex flex-wrap justify-center gap-2.5">
          {needsTap ? (
            <>
              <HudButton primary color={color} onClick={onTap}>
                <AudioLines className="size-4" />
                Resume Lumen
              </HudButton>
              <HudButton color={color} onClick={onClose}>
                Not now
              </HudButton>
            </>
          ) : active ? (
            <>
              {busy && (
                <HudButton color={color} onClick={onInterrupt}>
                  <Square className="size-3.5" />
                  Stop, Lumen
                </HudButton>
              )}
              <HudButton color={color} onClick={onThankYou}>
                Thank you, Lumen
              </HudButton>
            </>
          ) : (
            canTalk && (
              <HudButton primary color={color} onClick={onTap}>
                <AudioLines className="size-4" />
                Talk to Lumen
              </HudButton>
            )
          )}
        </div>
      </footer>
    </div>
  )
}

/**
 * Lumen as it has always appeared from the chat on its own: over the whole page,
 * only while it runs or asks to be resumed.
 *
 * A portal to <body>: the chat panel is glass (backdrop-filter), and any filter
 * makes an ancestor the containing block for `position: fixed` — the full-screen
 * HUD would otherwise be trapped inside the sidebar.
 */
export function AutopilotOverlay(props: Omit<LumenStageProps, 'active' | 'layout'>) {
  return createPortal(<LumenStage {...props} active={!props.needsTap} layout="overlay" />, document.body)
}

/** Before the first word: the greeting, and the one click a browser needs. */
function Waiting({
  greeting,
  canTalk,
  ink,
  color,
  docked,
}: {
  greeting?: string
  canTalk: boolean
  ink: string
  color: string
  docked: boolean
}) {
  return (
    <>
      {greeting && (
        <BlurWords
          // A new greeting (the disputes loaded) plays again rather than half-swapping.
          key={greeting}
          text={greeting}
          className={cn(
            'justify-center text-center font-light tracking-tight',
            docked ? 'text-xl' : 'text-2xl sm:text-3xl',
          )}
          color={inkAt(ink, 95)}
        />
      )}
      {canTalk ? (
        <ShinyText
          text="Tap to talk · speak, pause, and Lumen answers"
          color={inkHex(ink, 55)}
          shineColor={color}
          speed={3.2}
          className="text-sm tracking-wide"
        />
      ) : (
        <p className="max-w-sm text-sm leading-relaxed" style={{ color: inkAt(ink, 55) }}>
          Voice is switched off on this server. Your memories and the chat are in the dock below.
        </p>
      )}
    </>
  )
}

/**
 * Lumen's presence: a living orb with a soft halo, the way an assistant shows
 * it is there — not instruments. It replaced an "arc reactor" of tick rings and
 * segmented arcs, which read as a dashboard rather than as someone listening.
 *
 * The orb is React Bits' Orb (WebGL) in the mode's colours; it swirls faster
 * while Lumen thinks or speaks and brightens while it hears you — props that
 * change with the state, not every frame. The halo is a canvas: one soft,
 * organic outline built from a few slow overlapping waves. It breathes when
 * idle, swells with your voice, pulses as Lumen speaks, and a single comet of
 * light circles it while it thinks. Every value eases toward its target at a
 * frame-rate independent rate, so a change of state never jumps.
 */
function Reactor({
  mode,
  color,
  orb,
  levelRef,
  compact,
  reduced,
}: {
  mode: HudMode
  color: string
  orb: string[]
  levelRef: RefObject<number>
  compact: boolean
  reduced: boolean
}) {
  const canvas = useRef<HTMLCanvasElement | null>(null)
  const modeRef = useRef(mode)
  modeRef.current = mode
  const colorRef = useRef(color)
  colorRef.current = color

  useEffect(() => {
    const el = canvas.current
    const ctx = el?.getContext('2d')
    if (!el || !ctx) return
    const fit = () => {
      const box = el.getBoundingClientRect()
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      el.width = Math.max(1, Math.round(box.width * dpr))
      el.height = Math.max(1, Math.round(box.height * dpr))
      // Drawn in a 400 x 400 space, whatever the size on screen.
      ctx.setTransform(el.width / 400, 0, 0, el.height / 400, 0, 0)
    }
    fit()
    const resize = new ResizeObserver(fit)
    resize.observe(el)

    let level = 0
    let busyness = 0
    let comet = 0
    let phase = 0
    let last = performance.now()
    let frame = 0
    const POINTS = 120

    const draw = (time: number) => {
      const dt = Math.min(0.05, (time - last) / 1000)
      last = time
      const t = time / 1000
      const current = modeRef.current
      const ease = (rate: number) => 1 - Math.exp(-dt * rate)
      const busy = current === 'thinking' || current === 'processing'
      const mic = Math.min(1, (levelRef.current ?? 0) * 14)
      const target =
        current === 'hearing' || current === 'listening'
          ? mic
          : current === 'speaking'
            ? 0.35 + 0.25 * Math.sin(t * 3.4) * Math.sin(t * 1.9 + 1)
            : current === 'idle'
              ? 0.08 + 0.06 * Math.sin(t * 1.2)
              : 0.12
      level += (target - level) * ease(5)
      busyness += ((busy ? 1 : 0) - busyness) * ease(2)
      phase += dt * (0.5 + busyness * 1.2 + level * 1.5)
      comet = (comet + dt * (0.6 + busyness * 2.2)) % (Math.PI * 2)

      const c = colorRef.current
      const base = 128 + level * 18
      const wobble = 0.025 + level * 0.11 + busyness * 0.02
      const radius = (a: number) =>
        base *
        (1 +
          wobble *
            (0.55 * Math.sin(3 * a + phase * 1.1) +
              0.3 * Math.sin(5 * a - phase * 1.6) +
              0.15 * Math.sin(8 * a + phase * 2.3)))
      const outline = () => {
        ctx.beginPath()
        for (let i = 0; i <= POINTS; i += 1) {
          const a = (i / POINTS) * Math.PI * 2
          const r = radius(a)
          const x = 200 + Math.cos(a) * r
          const y = 200 + Math.sin(a) * r
          if (i === 0) ctx.moveTo(x, y)
          else ctx.lineTo(x, y)
        }
        ctx.closePath()
      }

      ctx.clearRect(0, 0, 400, 400)
      // The halo: a wide, faint glow…
      outline()
      ctx.shadowColor = c
      ctx.shadowBlur = 38 + level * 40
      ctx.lineWidth = 10
      ctx.strokeStyle = `${c}${hex(0.16 + level * 0.3)}`
      ctx.stroke()
      // …and a fine bright line on it.
      ctx.shadowBlur = 10
      ctx.lineWidth = 1.6
      ctx.strokeStyle = `${c}${hex(0.45 + level * 0.45)}`
      ctx.stroke()
      // A second, slower echo just outside it.
      ctx.save()
      ctx.translate(200, 200)
      ctx.scale(1.12 + level * 0.06, 1.12 + level * 0.06)
      ctx.rotate(-phase * 0.15)
      ctx.translate(-200, -200)
      outline()
      ctx.shadowBlur = 0
      ctx.lineWidth = 1
      ctx.strokeStyle = `${c}${hex(0.12 + level * 0.15)}`
      ctx.stroke()
      ctx.restore()

      // Thinking: one comet of light round the halo.
      if (busyness > 0.02) {
        const tail = 1.1
        for (let k = 0; k < 18; k += 1) {
          const a = comet - (k / 18) * tail
          const r = radius(a)
          ctx.beginPath()
          ctx.arc(200 + Math.cos(a) * r, 200 + Math.sin(a) * r, 2.6 - k * 0.12, 0, Math.PI * 2)
          ctx.fillStyle = `${c}${hex(busyness * (1 - k / 18) * 0.9)}`
          ctx.shadowBlur = 12
          ctx.fill()
        }
      }
      if (!reduced) frame = requestAnimationFrame(draw)
    }
    frame = requestAnimationFrame(draw)
    return () => {
      cancelAnimationFrame(frame)
      resize.disconnect()
    }
  }, [levelRef, reduced])

  const busy = mode === 'thinking' || mode === 'processing'
  const lively = mode === 'speaking' || mode === 'hearing'

  return (
    <div className={cn('relative shrink-0', compact ? 'size-[min(64vw,280px)]' : 'size-[min(64vw,380px)]')}>
      {/* Ambient light behind it. */}
      <div
        className="absolute inset-[8%] rounded-full blur-3xl transition-[background] duration-700"
        style={{ background: `radial-gradient(circle, ${color}40, transparent 70%)` }}
      />
      {/* The halo. */}
      <canvas ref={canvas} aria-hidden className="absolute inset-0 size-full" />
      {/* The orb itself (React Bits). */}
      <div className="absolute inset-[22%]">
        <Orb
          colors={orb}
          backgroundColor="#000000"
          glow={lively ? 1.35 : 1}
          wobble={busy ? 1.6 : lively ? 1.3 : 0.9}
          speed={busy ? 1.7 : lively ? 1.3 : 0.7}
          hoverIntensity={0.4}
          rotateOnHover={false}
          followCursor={false}
          clickPulse={false}
          forceHoverState={lively}
          paused={reduced}
          dpr={1}
        />
      </div>
    </div>
  )
}

/** An opacity (0-1) as the two hex digits appended to a colour. */
function hex(alpha: number): string {
  return Math.round(Math.max(0, Math.min(1, alpha)) * 255)
    .toString(16)
    .padStart(2, '0')
}

/** A long answer, in full and formatted, beside Lumen; it follows the text as it is written. */
function AnswerCard({ text, color, ink, docked }: { text: string; color: string; ink: string; docked: boolean }) {
  const scroll = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const view = scroll.current
    if (view) view.scrollTop = view.scrollHeight
  }, [text])
  return (
    <section
      aria-label="Lumen's answer"
      className={cn(
        'flex min-h-0 flex-col rounded-2xl border backdrop-blur-md animate-fade-up',
        docked ? 'max-h-[38%] w-full' : 'max-h-[70%] w-[min(42%,520px)] shrink-0',
      )}
      style={{ borderColor: `${color}30`, background: `${color}0b` }}
    >
      <span
        className="px-5 pt-3.5 pb-1 font-mono text-[10px] tracking-[0.25em] uppercase"
        style={{ color: `${color}aa` }}
      >
        Lumen
      </span>
      <div ref={scroll} className="scrollbar-slim min-h-0 overflow-y-auto px-5 pb-4" style={{ color: inkAt(ink, 90) }}>
        <Markdown>{text}</Markdown>
      </div>
    </section>
  )
}

/** The answer as it is spoken: the last couple of lines, fading in from the left. */
function Subtitle({ text, color, ink }: { text: string; color: string; ink: string }) {
  const tail = text.length > 260 ? `…${text.slice(-260).replace(/^\S*\s/, '')}` : text
  return (
    <div
      className="relative max-w-2xl rounded-2xl border px-5 py-3.5 text-center backdrop-blur-md"
      style={{ borderColor: `${color}30`, background: `${color}0d` }}
    >
      <span
        className="mb-1 block font-mono text-[10px] tracking-[0.25em] uppercase"
        style={{ color: `${color}aa` }}
      >
        Lumen
      </span>
      <p className="text-[15px] leading-relaxed" style={{ color: inkAt(ink, 90) }}>
        {tail}
      </p>
    </div>
  )
}

function HudBackdrop({
  color,
  aurora,
  paused,
  docked,
}: {
  color: string
  aurora: string[]
  paused: boolean
  docked: boolean
}) {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0">
      {/* The mode's aurora, drifting above the horizon (React Bits). */}
      <div className="absolute inset-0 opacity-60">
        <Aurora
          colorStops={aurora}
          amplitude={docked ? 0.8 : 1.1}
          blend={0.55}
          speed={0.6}
          height={0.4}
          paused={paused}
          mouseInteraction={false}
          // A soft gradient: drawn at half resolution and scaled up it looks the
          // same, for a quarter of the pixels. It was most of the frame time.
          dpr={0.5}
        />
      </div>
      {/* Colour wash from the centre. */}
      <div
        className="absolute inset-0 transition-[background] duration-700"
        style={{ background: `radial-gradient(ellipse at 50% 45%, ${color}1f, transparent 60%)` }}
      />
      {/* Vignette. */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,transparent_45%,#000_100%)] opacity-80" />
    </div>
  )
}

function HudButton({
  children,
  onClick,
  color,
  primary = false,
}: {
  children: React.ReactNode
  onClick: () => void
  color: string
  primary?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'flex items-center gap-2 rounded-full text-sm font-medium tracking-wide transition-all hover:brightness-125 active:scale-[0.97]',
        primary ? 'h-11 px-6' : 'h-10 px-5',
      )}
      style={
        primary
          ? { background: color, color: '#03070b', boxShadow: `0 0 34px -4px ${color}` }
          : { color, background: `${color}10`, boxShadow: `inset 0 0 0 1px ${color}45` }
      }
    >
      {children}
    </button>
  )
}

function Kbd({ children, ink }: { children: React.ReactNode; ink: string }) {
  return (
    <span
      className="mx-0.5 rounded border px-1.5 py-px whitespace-nowrap"
      style={{ borderColor: inkAt(ink, 20), background: inkAt(ink, 5), color: inkAt(ink, 70) }}
    >
      {children}
    </span>
  )
}
