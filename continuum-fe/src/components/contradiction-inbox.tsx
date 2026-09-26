import { Check, Loader2, Scale } from 'lucide-react'
import { useState } from 'react'

import { EmptyPanel } from '@/components/memory-detail'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import { CATEGORY_LABEL } from '@/lib/memory-style'
import type { ConflictPair, Memory } from '@/lib/types'
import type { Resource } from '@/hooks/use-resource'
import type { ConflictListResponse } from '@/lib/types'

/**
 * The contradiction inbox.
 *
 * Phase 2's answer to ambiguity is a human, so there has to be somewhere for the
 * ambiguity to land — and somewhere for the human to answer it. This is that
 * screen, and it exists because the resolver deliberately refused to guess.
 *
 * Three verdicts, and **keep both is not a cop-out**. A great many apparent
 * contradictions are two things that are simply both true — "Postgres for
 * reporting" and "Mongo for the event store" only look like a fight. Making that
 * a first-class button, rather than burying it behind a skip, is the difference
 * between a review queue people use and one they abandon.
 */
export function ContradictionInbox({
  resource,
  onResolved,
  onSelect,
}: {
  resource: Resource<ConflictListResponse>
  onResolved: () => void
  onSelect: (id: string) => void
}) {
  const [pending, setPending] = useState<string | null>(null)

  const resolve = async (
    pair: ConflictPair,
    body: { winner_id: string; loser_ids: string[]; keep_both: boolean },
  ) => {
    setPending(pair.memory.id)
    try {
      await api.resolveConflict(body)
      resource.refresh()
      onResolved()
    } finally {
      setPending(null)
    }
  }

  if (resource.loading && !resource.data) {
    return <Centered>Loading the inbox…</Centered>
  }
  if (resource.error) {
    return <EmptyPanel title="Could not load conflicts" body={resource.error} />
  }

  const pairs = resource.data?.conflicts ?? []
  if (pairs.length === 0) {
    return (
      <EmptyPanel
        title="Nothing to decide"
        body="No unresolved contradictions. When the resolver is not confident enough to retire a belief on its own, the pair lands here instead of being guessed at."
      />
    )
  }

  return (
    <div className="scrollbar-slim h-full overflow-y-auto px-4 py-4">
      <p className="mb-3 text-xs leading-relaxed text-muted">
        {pairs.length} unresolved {pairs.length === 1 ? 'disagreement' : 'disagreements'}. The
        system stopped rather than pick a side.
      </p>

      <ul className="space-y-3">
        {pairs.map((pair) => {
          const busy = pending === pair.memory.id
          const sides = [pair.memory, ...pair.conflicting]

          return (
            <li key={pair.memory.id} className="panel overflow-hidden">
              <div className="divide-y divide-border">
                {sides.map((side) => (
                  <Side
                    key={side.id}
                    memory={side}
                    busy={busy}
                    onInspect={() => onSelect(side.id)}
                    onWins={() =>
                      void resolve(pair, {
                        winner_id: side.id,
                        loser_ids: sides.filter((s) => s.id !== side.id).map((s) => s.id),
                        keep_both: false,
                      })
                    }
                  />
                ))}
              </div>

              <div className="flex items-center gap-2 border-t border-border bg-surface-raised/40 px-3 py-2">
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  onClick={() =>
                    void resolve(pair, {
                      winner_id: pair.memory.id,
                      loser_ids: pair.conflicting.map((m) => m.id),
                      keep_both: true,
                    })
                  }
                >
                  {busy ? <Loader2 className="animate-spin" /> : <Scale />}
                  Both are true
                </Button>
                <span className="text-[11px] leading-tight text-muted/70">
                  Clears the dispute, keeps both beliefs active.
                </span>
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

function Side({
  memory,
  busy,
  onWins,
  onInspect,
}: {
  memory: Memory
  busy: boolean
  onWins: () => void
  onInspect: () => void
}) {
  return (
    <div className="px-3 py-2.5">
      <div className="mb-1.5 flex items-center gap-1.5">
        <Badge>{CATEGORY_LABEL[memory.category]}</Badge>
        <Badge className="tabular-nums">{memory.confidence.toFixed(2)}</Badge>
        <span className="ml-auto text-[10px] text-muted/70">
          {new Date(memory.created_at).toLocaleDateString()}
        </span>
      </div>

      <p className="text-xs leading-relaxed text-foreground">{memory.content}</p>

      {memory.source_excerpt && (
        <p className="mt-1 text-[11px] leading-relaxed text-muted/80 italic">
          “{memory.source_excerpt}”
        </p>
      )}

      <div className="mt-2 flex items-center gap-1.5">
        <Button size="sm" variant="secondary" disabled={busy} onClick={onWins}>
          {busy ? <Loader2 className="animate-spin" /> : <Check />}
          This one holds
        </Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={onInspect}>
          Inspect
        </Button>
      </div>
    </div>
  )
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="flex h-full items-center justify-center text-xs text-muted">{children}</div>
}
