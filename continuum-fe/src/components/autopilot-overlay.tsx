import { AudioLines, Loader2, Square, X } from 'lucide-react'
import { type RefObject, useEffect, useRef } from 'react'

import { Button } from '@/components/ui/button'
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

/**
 * Lumen's screen — the hands-free voice mode. It covers the conversation while it runs; the turns are
 * still being written underneath, so ending autopilot reveals the whole
 * transcript — what was said and what was answered — in the chat.
 */
export function AutopilotOverlay({
  phase,
  stage,
  caption,
  levelRef,
  needsTap,
  onTap,
  onInterrupt,
  onThankYou,
  onClose,
}: {
  phase: PilotPhase
  stage: PilotStage
  caption: string | null
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
  const orbRef = useRef<HTMLDivElement | null>(null)

  // The orb breathes with the microphone level — straight to the DOM, not
  // through React state, because it changes every frame.
  useEffect(() => {
    let frame = 0
    const draw = () => {
      const orb = orbRef.current
      if (orb) {
        const listening = phase === 'listening' || phase === 'hearing'
        const level = listening ? Math.min(1, (levelRef.current ?? 0) * 12) : 0
        orb.style.transform = `scale(${1 + level * 0.45})`
      }
      frame = requestAnimationFrame(draw)
    }
    frame = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(frame)
  }, [levelRef, phase])

  const label = needsTap
    ? 'Lumen was on last time'
    : phase === 'working'
      ? (stage && STAGE_LABEL[stage]) || 'Working…'
      : LABEL[phase]
  const busy = phase === 'starting' || (phase === 'working' && stage !== 'speaking')

  return (
    <div
      role="dialog"
      aria-label="Lumen"
      className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-5 bg-background/95 px-6 text-center backdrop-blur-sm"
    >
      <div className="relative flex size-36 items-center justify-center">
        <div
          ref={orbRef}
          className={cn(
            'absolute inset-4 rounded-full transition-colors duration-300',
            phase === 'hearing' ? 'bg-accent/40' : 'bg-accent/20',
            stage === 'speaking' && phase === 'working' && 'animate-pulse bg-accent/35',
          )}
        />
        <div className="relative flex size-16 items-center justify-center rounded-full bg-accent/90 text-background shadow-lg">
          {busy ? <Loader2 className="size-7 animate-spin" /> : <AudioLines className="size-7" />}
        </div>
      </div>

      <div className="space-y-1" aria-live="polite">
        <p className="text-sm font-medium text-foreground">{label}</p>
        {caption && !needsTap && (
          <p className="mx-auto max-w-xs text-xs leading-relaxed text-muted italic">“{caption}”</p>
        )}
        {!needsTap && (phase === 'listening' || phase === 'hearing') && (
          <p className="mx-auto max-w-xs text-[11px] leading-relaxed text-muted/70">
            Speak, then pause for two seconds. Say “thank you, Lumen” when you are done.
          </p>
        )}
        {!needsTap && phase === 'working' && (stage === 'thinking' || stage === 'speaking') && (
          <p className="mx-auto max-w-xs text-[11px] leading-relaxed text-muted/70">
            Say “stop, Lumen” to interrupt.
          </p>
        )}
      </div>

      <div className="flex gap-2">
        {needsTap ? (
          <Button size="sm" onClick={onTap}>
            <AudioLines />
            Resume Lumen
          </Button>
        ) : (
          (stage === 'speaking' || stage === 'thinking') && (
            <Button size="sm" variant="secondary" onClick={onInterrupt}>
              <Square />
              Stop, Lumen
            </Button>
          )
        )}
        {needsTap ? (
          <Button size="sm" variant="ghost" onClick={onClose}>
            <X />
            Not now
          </Button>
        ) : (
          <Button size="sm" variant="ghost" onClick={onThankYou}>
            <X />
            Thank you, Lumen
          </Button>
        )}
      </div>
    </div>
  )
}
