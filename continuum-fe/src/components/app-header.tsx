import { Archive, History, LogOut, RefreshCw, Settings, X } from 'lucide-react'

import { Wordmark } from '@/components/brand'
import { Button } from '@/components/ui/button'
import { Tooltip } from '@/components/ui/tooltip'
import type { HealthResponse, Me } from '@/lib/types'
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
}) {
  const today = localDay(new Date())
  return (
    <header className="flex h-14 shrink-0 items-center gap-4 border-b border-border/60 px-4">
      <Wordmark />

      <span className="hidden items-center gap-1.5 rounded-full bg-surface px-2.5 py-1 text-[11px] text-muted tabular-nums md:flex">
        <HealthDot health={health} />
        {nodeCount} beliefs · {edgeCount} edges
      </span>

      <div className="ml-auto flex items-center gap-1">
        <Tooltip label="See what was believed on a past day. The graph and the chat both go back; nothing said meanwhile is remembered.">
          <label
            className={cn(
              'flex h-8 cursor-pointer items-center gap-1.5 rounded-lg px-2.5 text-xs transition-colors',
              asOf
                ? 'bg-amber-500/15 text-amber-200 ring-1 ring-amber-400/30'
                : 'text-muted hover:bg-surface-raised hover:text-foreground',
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
            className={cn(includeArchived && 'text-foreground')}
          >
            <Archive />
          </Button>
        </Tooltip>

        <Tooltip label="Refresh">
          <Button size="icon" variant="ghost" onClick={onRefresh} aria-label="Refresh">
            <RefreshCw className={cn(refreshing && 'animate-spin')} />
          </Button>
        </Tooltip>

        <span className="mx-1.5 h-5 w-px bg-border" />

        <Tooltip label="Settings — voice, API keys, password, your data">
          <button
            type="button"
            onClick={onAccount}
            className="flex h-8 max-w-60 items-center gap-2 rounded-lg pr-2.5 pl-1 text-xs text-muted transition-colors hover:bg-surface-raised hover:text-foreground"
          >
            <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-accent/15 text-[11px] font-semibold text-accent uppercase">
              {me.email.charAt(0)}
            </span>
            <span className="hidden truncate sm:inline">{me.email}</span>
            <Settings className="size-3.5 shrink-0 opacity-60" />
          </button>
        </Tooltip>

        <Tooltip label="Sign out">
          <Button size="icon" variant="ghost" onClick={onSignOut} aria-label="Sign out">
            <LogOut />
          </Button>
        </Tooltip>
      </div>
    </header>
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
