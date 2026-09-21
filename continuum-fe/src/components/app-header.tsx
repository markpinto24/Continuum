import { Archive, RefreshCw, User } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Tooltip } from '@/components/ui/tooltip'
import type { HealthResponse } from '@/lib/types'
import { cn } from '@/lib/utils'

export function AppHeader({
  userId,
  onUserId,
  health,
  nodeCount,
  edgeCount,
  includeArchived,
  onIncludeArchived,
  onRefresh,
  refreshing,
}: {
  userId: string
  onUserId: (value: string) => void
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

      <div className="ml-2 flex items-center gap-1.5">
        <User className="size-3.5 text-muted" />
        <Input
          value={userId}
          onChange={(event) => onUserId(event.target.value)}
          placeholder="user id"
          className="h-7 w-32"
          aria-label="User id"
        />
      </div>

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
      </div>
    </header>
  )
}

function HealthDot({ health }: { health: HealthResponse | null }) {
  const label = health
    ? `Qdrant ${health.qdrant} · LLM ${health.llm} · ${health.environment}`
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
