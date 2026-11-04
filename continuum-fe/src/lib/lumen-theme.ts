import type { Mode } from '@/lib/types'

/** What Lumen is doing, which decides its colour and motion. */
export type HudMode = 'idle' | 'listening' | 'hearing' | 'processing' | 'thinking' | 'speaking'

export interface LumenTheme {
  /** One colour per state: listening, hearing you, transcribing, thinking, speaking. */
  colors: Record<HudMode, string>
  /** The screen behind it. */
  base: string
  /** Text on that screen. */
  ink: string
  /** The aurora drifting behind Lumen. */
  aurora: string[]
  /** The orb at its centre (React Bits Orb): its three swirling colours. */
  orb: string[]
}

/**
 * Lumen in each mode's colours, so it is plain which memory it is talking from.
 * Work keeps the cyan it always had; Personal is the rose of the Personal accent
 * (`--color-accent` under `[data-mode='personal']`). The states keep their
 * meaning across both — transcribing is warm, thinking is violet — only the
 * family changes.
 */
export const LUMEN_THEMES: Record<Mode, LumenTheme> = {
  workspace: {
    colors: {
      idle: '#67e8f9',
      listening: '#67e8f9',
      hearing: '#a5f3fc',
      processing: '#fcd34d',
      thinking: '#c4b5fd',
      speaking: '#5eead4',
    },
    base: '#03070b',
    ink: '#ecfeff',
    aurora: ['#0e7490', '#22d3ee', '#4f46e5'],
    orb: ['#22d3ee', '#6366f1', '#0891b2'],
  },
  personal: {
    colors: {
      idle: '#fda4af',
      listening: '#fda4af',
      hearing: '#fecdd3',
      processing: '#fdba74',
      thinking: '#f0abfc',
      speaking: '#f9a8d4',
    },
    base: '#0b0508',
    ink: '#fff1f2',
    aurora: ['#9f1239', '#fb7185', '#9333ea'],
    orb: ['#fb7185', '#c084fc', '#be185d'],
  },
}

/** `ink` at an opacity, for the HUD's quieter lines of text. */
export function inkAt(ink: string, percent: number): string {
  return `color-mix(in oklch, ${ink} ${percent}%, transparent)`
}

/** The same as a hex colour with alpha, for what reads colour through a canvas
 *  (React Bits' ShinyText), which does not understand `color-mix`: it drew it black. */
export function inkHex(ink: string, percent: number): string {
  const alpha = Math.round((percent / 100) * 255)
  return `${ink}${alpha.toString(16).padStart(2, '0')}`
}
