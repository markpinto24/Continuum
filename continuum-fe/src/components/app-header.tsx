import { Archive, Briefcase, Heart, History, LogOut, RefreshCw, Settings, X } from 'lucide-react'

import { Wordmark } from '@/components/brand'
import { Button } from '@/components/ui/button'
import { Tooltip } from '@/components/ui/tooltip'
import type { HealthResponse, Me, Mode } from '@/lib/types'
import { cn } from '@/lib/utils'

/**
 * The top bar: who you are, when you are looking at, and the few switches that
 * change what the graph shows. Everything else lives in the sidebar.
 */
export function AppHeader({
  me,
  onAccount,
  onSignOut,
  health,
  nodeCount,
  edgeCount,
  includeArchived,
  onIncludeArchived,
  onRefresh,
  refreshing,
  asOf,
  onAsOf,
  mode,
  onMode,
  graphControls = true,
}: {
  me: Me
  onAccount: () => void
  onSignOut: () => void
  health: HealthResponse | null
  nodeCount: number
  edgeCount: number
  includeArchived: boolean
  onIncludeArchived: (value: boolean) => void
  onRefresh: () => void
  refreshing: boolean
  /** A past day (YYYY-MM-DD) to view beliefs as they stood, or null for now. */
  asOf: string | null
  onAsOf: (day: string | null) => void
  /** Personal or Work: which of your two graphs everything below shows. */
  mode: Mode
  onMode: (mode: Mode) => void
  /** The graph is on screen: its counts and its date, archive and refresh controls. */
  graphControls?: boolean
}) {
  const today = localDay(new Date())
  return (
    // Part of the backdrop, not a grey strip over it: a wash of the mode's own
    // colour (--backdrop-a/-b, app-backdrop.tsx) fading down, a light blur so the
    // glows still show through, and a hairline in the same colours.
    <header className="relative z-20 flex h-14 shrink-0 items-center gap-2 bg-[linear-gradient(to_bottom,color-mix(in_oklch,var(--backdrop-b)_14%,transparent),color-mix(in_oklch,var(--backdrop-a)_5%,transparent))] px-2 backdrop-blur-md sm:gap-3 sm:px-4">
      <span
        aria-hidden
        className="pointer-events-none absolute inset-x-0 bottom-0 h-px bg-[linear-gradient(to_right,transparent,color-mix(in_oklch,var(--backdrop-a)_55%,transparent),color-mix(in_oklch,var(--backdrop-b)_55%,transparent),transparent)]"
      />
      <Wordmark />

      <ModeSwitch mode={mode} onMode={onMode} />

      <span
        className={cn(
          'hidden h-8 items-center gap-2 rounded-full bg-white/[0.04] px-3 text-[11px] text-muted tabular-nums ring-1 ring-white/[0.08]',
          graphControls && 'md:flex',
        )}
      >
        <HealthDot health={health} />
        {nodeCount} beliefs · {edgeCount} edges
      </span>

      {/* On a phone the controls scroll within the header rather than spill off it. */}
      <div className="scrollbar-none ml-auto flex min-w-0 items-center gap-1.5 overflow-x-auto">
        <div
          className={cn(
            'h-9 shrink-0 items-center gap-0.5 rounded-full bg-white/[0.04] px-1 ring-1 ring-white/[0.08]',
            graphControls ? 'flex' : 'hidden',
          )}
        >
          <Tooltip label="See what was believed on a past day. The graph and the chat both go back; nothing said meanwhile is remembered.">
            <label
              className={cn(
                'flex h-7 cursor-pointer items-center gap-1.5 rounded-full px-2.5 text-xs transition-colors',
                asOf
                  ? 'bg-amber-500/15 text-amber-200 ring-1 ring-amber-400/30'
                  : 'text-muted hover:bg-white/[0.06] hover:text-foreground',
              )}
            >
              <History className="size-3.5 shrink-0" />
              <span className="sr-only">As of</span>
              <input
                type="date"
                aria-label="As of"
                max={today}
                // Shows today when looking at the present. Today (or clearing the
                // field) means "now", not a snapshot taken at the end of today.
                value={asOf ?? today}
                onChange={(e) => {
                  const day = e.target.value
                  onAsOf(day && day < today ? day : null)
                }}
                className="w-[6.9rem] cursor-pointer bg-transparent text-xs tabular-nums outline-none [color-scheme:dark]"
              />
              {asOf && (
                <button
                  type="button"
                  aria-label="Back to now"
                  onClick={() => onAsOf(null)}
                  className="rounded p-0.5 hover:bg-amber-400/20"
                >
                  <X className="size-3" />
                </button>
              )}
            </label>
          </Tooltip>

          <Tooltip
            label={
              includeArchived
                ? 'Archived beliefs are shown. They decayed out of retrieval but were never deleted.'
                : 'Show archived beliefs. They still exist and can be restored.'
            }
          >
            <Button
              size="icon"
              variant={includeArchived ? 'secondary' : 'ghost'}
              aria-label="Archived"
              aria-pressed={includeArchived}
              onClick={() => onIncludeArchived(!includeArchived)}
              className={cn('size-7 rounded-full', includeArchived && 'text-foreground')}
            >
              <Archive />
            </Button>
          </Tooltip>

          <Tooltip label="Refresh">
            <Button size="icon" variant="ghost" onClick={onRefresh} aria-label="Refresh" className="size-7 rounded-full">
              <RefreshCw className={cn(refreshing && 'animate-spin')} />
            </Button>
          </Tooltip>
        </div>

        <Tooltip label="Settings — voice, API keys, password, your data">
          <button
            type="button"
            onClick={onAccount}
            // The email is hidden on a phone; the name must not be.
            aria-label={`Account and settings — ${me.email}`}
            className="flex h-9 max-w-60 shrink-0 items-center gap-2 rounded-full bg-white/[0.04] pr-3 pl-1 text-xs text-muted ring-1 ring-white/[0.08] transition-colors hover:bg-white/[0.08] hover:text-foreground"
          >
            <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-accent/15 text-[11px] font-semibold text-accent uppercase">
              {me.email.charAt(0)}
            </span>
            <span className="hidden truncate sm:inline">{me.email}</span>
            <Settings className="size-3.5 shrink-0 opacity-60" />
          </button>
        </Tooltip>

        <Tooltip label="Sign out">
          <Button
            size="icon"
            variant="ghost"
            onClick={onSignOut}
            aria-label="Sign out"
            className="size-9 shrink-0 rounded-full bg-white/[0.04] ring-1 ring-white/[0.08] hover:bg-white/[0.08]"
          >
            <LogOut />
          </Button>
        </Tooltip>
      </div>
    </header>
  )
}

const MODES: { value: Mode; label: string; icon: typeof Heart; hint: string }[] = [
  {
    value: 'personal',
    label: 'Personal',
    icon: Heart,
    hint: 'Your life: people, health, routines, goals. Private to you — never shared.',
  },
  {
    value: 'workspace',
    label: 'Work',
    icon: Briefcase,
    hint: 'Your work: decisions, owners, constraints — and the team’s shared space.',
  },
]

/**
 * A segmented control. Two separate graphs, not a filter: switching changes the
 * graph, chat, inbox and Lumen together, and nothing crosses between them.
 */
export function ModeSwitch({ mode, onMode }: { mode: Mode; onMode: (mode: Mode) => void }) {
  return (
    <div
      role="radiogroup"
      aria-label="Mode"
      className="flex h-9 items-center gap-0.5 rounded-full bg-white/[0.04] p-1 ring-1 ring-white/[0.08] backdrop-blur-md"
    >
      {MODES.map(({ value, label, icon: Icon, hint }) => {
        const active = value === mode
        return (
          <Tooltip key={value} label={hint}>
            <button
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => !active && onMode(value)}
              className={cn(
                'flex h-7 items-center gap-1.5 rounded-full px-3 text-xs font-medium transition-all',
                active
                  ? 'bg-accent/15 text-accent shadow-[inset_0_0_0_1px_color-mix(in_oklch,var(--color-accent)_35%,transparent)]'
                  : 'text-muted hover:text-foreground',
              )}
            >
              <Icon className="size-3.5" />
              <span className="sr-only sm:not-sr-only">{label}</span>
            </button>
          </Tooltip>
        )
      })}
    </div>
  )
}

/** YYYY-MM-DD in the viewer's own timezone — not UTC, which is a day behind
 *  in the early hours east of Greenwich. */
function localDay(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function HealthDot({ health }: { health: HealthResponse | null }) {
  const label = health
    ? `Qdrant ${health.qdrant} · Postgres ${health.database} · LLM ${health.llm} · ${health.environment}`
    : 'Cannot reach the API. Is the stack up?'
  const tone = !health ? 'bg-danger' : health.status === 'ok' ? 'bg-emerald-400' : 'bg-amber-400'

  return (
    <Tooltip label={label}>
      <span className="relative flex size-2 cursor-help">
        {health?.status === 'ok' && (
          <span className="absolute inset-0 animate-ping rounded-full bg-emerald-400/50 [animation-duration:2.5s]" />
        )}
        <span className={cn('relative size-2 rounded-full', tone)} />
      </span>
    </Tooltip>
  )
}
