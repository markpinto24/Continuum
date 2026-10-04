import { AudioLines, Square, X } from 'lucide-react'
import { type RefObject, useEffect, useMemo, useRef } from 'react'
import { createPortal } from 'react-dom'

import type { PilotPhase } from '@/hooks/use-autopilot'
import { cn } from '@/lib/utils'

export type PilotStage = 'transcribing' | 'thinking' | 'speaking' | 'greeting' | null

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

/** What the HUD is doing, which decides its colour and motion. */
type Mode = 'idle' | 'listening' | 'hearing' | 'processing' | 'thinking' | 'speaking'

const MODE_COLOR: Record<Mode, string> = {
  idle: '#67e8f9',
  listening: '#67e8f9', // cyan
  hearing: '#a5f3fc', // brighter while your voice is coming in
  processing: '#fcd34d', // amber while Whisper transcribes
  thinking: '#c4b5fd', // violet while the answer is being written
  speaking: '#5eead4', // teal while it talks
}

const MODE_TAG: Record<Mode, string> = {
  idle: 'Standby',
  listening: 'Online',
  hearing: 'Voice detected',
  processing: 'Transcribing',
  thinking: 'Processing',
  speaking: 'Responding',
}

const BARS = 96

/**
 * Lumen's screen — the hands-free voice mode, as a heads-up display.
 *
 * It covers everything while it runs; the turns are still being written into
 * the chat underneath, so ending it reveals the whole transcript. The motion is
 * driven straight to the DOM from the microphone level (`levelRef`), not through
 * React state — it changes every frame.
 */
export function AutopilotOverlay({
  phase,
  stage,
  caption,
  answer,
  levelRef,
  needsTap,
  onTap,
  onInterrupt,
  onThankYou,
  onClose,
}: {
  phase: PilotPhase
  stage: PilotStage
  /** What it heard you say last. */
  caption: string | null
  /** What it is saying now, as it is written. */
  answer?: string | null
  levelRef: RefObject<number>
  /** Restored from last time: the browser needs a click before it may speak or listen. */
  needsTap: boolean
  onTap: () => void
  onInterrupt: () => void
  /** "Thank you, Lumen": it says goodbye and the session ends. */
  onThankYou: () => void
  /** Dismiss the resume prompt without starting. */
  onClose: () => void
}) {
  const mode: Mode = needsTap
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
  const color = MODE_COLOR[mode]

  const label = needsTap
    ? 'Lumen was on last time'
    : phase === 'working'
      ? (stage && STAGE_LABEL[stage]) || 'Working…'
      : LABEL[phase]
  const busy = stage === 'speaking' || stage === 'thinking'

  // A portal to <body>: the chat panel is glass (backdrop-filter), and any
  // filter makes an ancestor the containing block for `position: fixed` — the
  // full-screen HUD would otherwise be trapped inside the sidebar.
  return createPortal(
    <div
      role="dialog"
      aria-label="Lumen"
      className="fixed inset-0 z-50 flex flex-col overflow-hidden bg-[#03070b] text-cyan-50 animate-fade-up"
      style={{ ['--hud' as string]: color }}
    >
      <HudBackdrop color={color} />

      <header className="relative flex items-center justify-between px-6 py-5">
        <div className="flex items-center gap-3">
          <span className="text-[13px] font-semibold tracking-[0.35em] text-cyan-100/90">LUMEN</span>
          <span
            className="flex items-center gap-1.5 rounded-full px-2.5 py-0.5 font-mono text-[10px] tracking-[0.18em] uppercase ring-1 transition-colors duration-500"
            style={{ color, boxShadow: `inset 0 0 0 1px ${color}55`, background: `${color}12` }}
          >
            <span className="size-1.5 rounded-full hud-breathe" style={{ background: color }} />
            {MODE_TAG[mode]}
          </span>
        </div>
        <button
          type="button"
          onClick={needsTap ? onClose : onThankYou}
          aria-label="Close Lumen"
          className="rounded-full p-2 text-cyan-100/50 transition-colors hover:bg-white/5 hover:text-cyan-50"
        >
          <X className="size-5" />
        </button>
      </header>

      <main className="relative flex min-h-0 flex-1 flex-col items-center justify-center gap-8 px-6">
        <Reactor mode={mode} color={color} levelRef={levelRef} />

        <div className="flex max-w-xl flex-col items-center gap-3 text-center" aria-live="polite">
          <p className="text-lg font-light tracking-wide text-cyan-50/95 transition-colors">{label}</p>
          {!needsTap && caption && (
            <p className="max-w-md text-sm leading-relaxed text-cyan-100/55">
              <span className="mr-2 font-mono text-[10px] tracking-[0.2em] text-cyan-200/40 uppercase">
                You
              </span>
              “{caption}”
            </p>
          )}
        </div>

        {!needsTap && answer && (stage === 'speaking' || stage === 'thinking') && (
          <Subtitle text={answer} color={color} />
        )}
      </main>

      <footer className="relative flex flex-col items-center gap-4 px-6 pb-8">
        {!needsTap && (
          <p className="font-mono text-[11px] tracking-wide text-cyan-100/40">
            {busy ? (
              <>
                Say <Kbd>stop, Lumen</Kbd> to interrupt
              </>
            ) : (
              <>
                Speak, then pause for two seconds · say <Kbd>thank you, Lumen</Kbd> when you are done
              </>
            )}
          </p>
        )}
        <div className="flex gap-2.5">
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
          ) : (
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
          )}
        </div>
      </footer>
    </div>,
    document.body,
  )
}

/**
 * The arc reactor: three rotating rings, a tick ring, a radial waveform that
 * follows the microphone, and a core that breathes with your voice.
 */
function Reactor({
  mode,
  color,
  levelRef,
}: {
  mode: Mode
  color: string
  levelRef: RefObject<number>
}) {
  const bars = useRef<(SVGLineElement | null)[]>([])
  const core = useRef<HTMLDivElement | null>(null)
  const glow = useRef<HTMLDivElement | null>(null)
  const modeRef = useRef(mode)
  modeRef.current = mode

  const angles = useMemo(
    () => Array.from({ length: BARS }, (_, i) => (i / BARS) * Math.PI * 2 - Math.PI / 2),
    [],
  )

  useEffect(() => {
    let frame = 0
    let smooth = 0
    const draw = (time: number) => {
      const t = time / 1000
      const current = modeRef.current
      const mic = Math.min(1, (levelRef.current ?? 0) * 14)
      // Speaking has no microphone signal to follow (its own voice is ignored),
      // so it gets a synthetic, speech-like envelope instead.
      const target =
        current === 'hearing' || current === 'listening'
          ? mic
          : current === 'speaking'
            ? 0.35 + 0.3 * Math.abs(Math.sin(t * 5.3) * Math.sin(t * 2.1 + 1))
            : current === 'thinking' || current === 'processing'
              ? 0.18
              : 0.05
      smooth += (target - smooth) * 0.18

      for (let i = 0; i < BARS; i += 1) {
        const line = bars.current[i]
        if (!line) continue
        const angle = angles[i]
        let wave: number
        if (current === 'thinking' || current === 'processing') {
          // A bright crest travelling round the ring.
          const crest = Math.cos(angle - t * 3.2)
          wave = 0.15 + Math.max(0, crest) ** 6 * 0.85
        } else {
          wave =
            0.25 +
            0.75 * Math.abs(Math.sin(i * 0.53 + t * 7) * Math.cos(i * 0.21 - t * 3.1)) * smooth * 1.6
        }
        const inner = 112
        const length = 4 + wave * 46 * (0.4 + smooth)
        line.setAttribute('x1', String(200 + Math.cos(angle) * inner))
        line.setAttribute('y1', String(200 + Math.sin(angle) * inner))
        line.setAttribute('x2', String(200 + Math.cos(angle) * (inner + length)))
        line.setAttribute('y2', String(200 + Math.sin(angle) * (inner + length)))
        line.setAttribute('stroke-opacity', String(0.25 + wave * 0.7))
      }

      const scale = 1 + smooth * 0.35
      if (core.current) core.current.style.transform = `scale(${scale})`
      if (glow.current) glow.current.style.opacity = String(0.35 + smooth * 0.65)
      frame = requestAnimationFrame(draw)
    }
    frame = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(frame)
  }, [angles, levelRef])

  const fast = mode === 'thinking' || mode === 'processing'

  return (
    <div className="relative size-[min(78vw,420px)]">
      {/* Ambient glow behind everything. */}
      <div
        ref={glow}
        className="absolute inset-[12%] rounded-full blur-3xl transition-colors duration-700"
        style={{ background: `radial-gradient(circle, ${color}55, transparent 70%)` }}
      />

      <svg viewBox="0 0 400 400" className="absolute inset-0 size-full overflow-visible">
        <defs>
          <filter id="hud-glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="2.2" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        <g stroke={color} fill="none" filter="url(#hud-glow)" className="transition-colors duration-700">
          {/* Outer frame: a thin dashed ring and four corner brackets. */}
          <g className="hud-spin" style={{ ['--hud-speed' as string]: fast ? '14s' : '60s' }}>
            <circle cx="200" cy="200" r="194" strokeOpacity="0.18" strokeWidth="1" strokeDasharray="2 6" />
            {[0, 90, 180, 270].map((deg) => (
              <path
                key={deg}
                d="M200 6 a194 194 0 0 1 40 4"
                strokeWidth="2.5"
                strokeOpacity="0.8"
                strokeLinecap="round"
                transform={`rotate(${deg} 200 200)`}
              />
            ))}
          </g>

          {/* Segmented arc ring, counter-rotating. */}
          <g className="hud-spin-reverse" style={{ ['--hud-speed' as string]: fast ? '6s' : '28s' }}>
            <circle
              cx="200"
              cy="200"
              r="176"
              strokeWidth="5"
              strokeOpacity="0.55"
              strokeDasharray="70 22 12 22 140 30"
              strokeLinecap="round"
            />
          </g>

          {/* Tick ring. */}
          <g className="hud-spin" style={{ ['--hud-speed' as string]: fast ? '10s' : '40s' }}>
            {Array.from({ length: 60 }, (_, i) => {
              const a = (i / 60) * Math.PI * 2
              const long = i % 5 === 0
              const r1 = long ? 150 : 155
              return (
                <line
                  key={i}
                  x1={200 + Math.cos(a) * r1}
                  y1={200 + Math.sin(a) * r1}
                  x2={200 + Math.cos(a) * 162}
                  y2={200 + Math.sin(a) * 162}
                  strokeWidth={long ? 1.6 : 0.8}
                  strokeOpacity={long ? 0.7 : 0.3}
                />
              )
            })}
          </g>

          {/* Inner thin ring with a single bright arc. */}
          <g className="hud-spin" style={{ ['--hud-speed' as string]: fast ? '2.4s' : '9s' }}>
            <circle cx="200" cy="200" r="104" strokeOpacity="0.2" strokeWidth="1" />
            <circle
              cx="200"
              cy="200"
              r="104"
              strokeWidth="2.5"
              strokeOpacity="0.95"
              strokeDasharray="60 593"
              strokeLinecap="round"
            />
          </g>

          {/* Radial waveform, written every frame. */}
          <g strokeWidth="2.2" strokeLinecap="round">
            {angles.map((_, i) => (
              <line
                key={i}
                ref={(el) => {
                  bars.current[i] = el
                }}
                x1="200"
                y1="200"
                x2="200"
                y2="200"
              />
            ))}
          </g>
        </g>
      </svg>

      {/* The core. */}
      <div className="absolute inset-0 flex items-center justify-center">
        {(mode === 'hearing' || mode === 'speaking') && (
          <>
            <span
              className="hud-ripple absolute size-[34%] rounded-full"
              style={{ boxShadow: `0 0 0 1.5px ${color}` }}
            />
            <span
              className="hud-ripple absolute size-[34%] rounded-full [animation-delay:1.1s]"
              style={{ boxShadow: `0 0 0 1.5px ${color}` }}
            />
          </>
        )}
        <div
          ref={core}
          className="relative size-[30%] rounded-full transition-[background,box-shadow] duration-700"
          style={{
            background: `radial-gradient(circle at 50% 45%, #ffffff 0%, ${color} 28%, ${color}66 55%, transparent 72%)`,
            boxShadow: `0 0 60px 10px ${color}55, inset 0 0 30px ${color}`,
          }}
        >
          <div
            className={cn('absolute inset-[18%] rounded-full', mode === 'idle' && 'hud-breathe')}
            style={{ boxShadow: `inset 0 0 0 1px #ffffff55` }}
          />
        </div>
      </div>
    </div>
  )
}

/** The answer as it is spoken: the last couple of lines, fading in from the left. */
function Subtitle({ text, color }: { text: string; color: string }) {
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
      <p className="text-[15px] leading-relaxed text-cyan-50/90">{tail}</p>
    </div>
  )
}

function HudBackdrop({ color }: { color: string }) {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0">
      {/* Fine grid. */}
      <div
        className="absolute inset-0 opacity-[0.07]"
        style={{
          backgroundImage: `linear-gradient(${color} 1px, transparent 1px), linear-gradient(90deg, ${color} 1px, transparent 1px)`,
          backgroundSize: '48px 48px',
          maskImage: 'radial-gradient(ellipse at center, black 20%, transparent 75%)',
        }}
      />
      {/* Colour wash from the centre. */}
      <div
        className="absolute inset-0 transition-[background] duration-700"
        style={{ background: `radial-gradient(ellipse at 50% 45%, ${color}1f, transparent 60%)` }}
      />
      {/* A slow scan line. */}
      <div className="absolute inset-x-0 top-0 h-full overflow-hidden">
        <div
          className="hud-scan h-1/3 w-full"
          style={{ background: `linear-gradient(to bottom, transparent, ${color}0d, transparent)` }}
        />
      </div>
      {/* Vignette. */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,transparent_45%,#000_100%)]" />
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
      className="flex h-10 items-center gap-2 rounded-full px-5 text-sm font-medium tracking-wide transition-all hover:brightness-125 active:scale-[0.97]"
      style={
        primary
          ? { background: color, color: '#03070b', boxShadow: `0 0 30px -4px ${color}` }
          : { color, background: `${color}10`, boxShadow: `inset 0 0 0 1px ${color}45` }
      }
    >
      {children}
    </button>
  )
}

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <span className="mx-0.5 rounded border border-cyan-200/20 bg-cyan-200/5 px-1.5 py-px text-cyan-100/70">
      {children}
    </span>
  )
}
