import { Archive, LogOut, RefreshCw, User } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Tooltip } from '@/components/ui/tooltip'
import type { HealthResponse, Me } from '@/lib/types'
import { cn } from '@/lib/utils'

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
}) {
  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b border-border px-4">
      <div className="flex items-baseline gap-2">
        <span className="text-sm font-semibold tracking-tight">Continuum</span>
        <span className="hidden text-[11px] text-muted sm:inline">belief graph</span>
      </div>

      <Tooltip label="Account, API keys and users">
        <Button size="sm" variant="ghost" className="ml-2 max-w-56" onClick={onAccount}>
          <User />
          <span className="truncate">{me.email}</span>
        </Button>
      </Tooltip>

      <span className="hidden text-[11px] text-muted tabular-nums md:inline">
        {nodeCount} beliefs · {edgeCount} edges
      </span>

      <div className="ml-auto flex items-center gap-1.5">
        <Tooltip
          label={
            includeArchived
              ? 'Archived beliefs are shown. They decayed out of retrieval but were never deleted.'
              : 'Archived beliefs are hidden. They still exist and can be restored.'
          }
        >
          <Button
            size="sm"
            variant={includeArchived ? 'secondary' : 'ghost'}
            onClick={() => onIncludeArchived(!includeArchived)}
          >
            <Archive />
            Archived
          </Button>
        </Tooltip>

        <Button size="icon" variant="ghost" onClick={onRefresh} aria-label="Refresh">
          <RefreshCw className={cn(refreshing && 'animate-spin')} />
        </Button>

        <HealthDot health={health} />

        <Button size="icon" variant="ghost" onClick={onSignOut} aria-label="Sign out">
          <LogOut />
        </Button>
      </div>
    </header>
  )
}

function HealthDot({ health }: { health: HealthResponse | null }) {
  const label = health
    ? `Qdrant ${health.qdrant} · Postgres ${health.database} · LLM ${health.llm} · ${health.environment}`
    : 'Cannot reach the API. Is the stack up?'
  const tone = !health ? 'bg-danger' : health.status === 'ok' ? 'bg-accent' : 'bg-amber-400'

  return (
    <Tooltip label={label}>
      <span className="flex size-8 cursor-help items-center justify-center">
        <span className={cn('size-2 rounded-full', tone)} />
      </span>
    </Tooltip>
  )
}
