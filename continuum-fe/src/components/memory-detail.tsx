import { ArrowUpRight, Loader2, Quote, RotateCcw, ThumbsUp } from 'lucide-react'
import { useCallback, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { useResource } from '@/hooks/use-resource'
import { api } from '@/lib/api'
import {
  CATEGORY_HALF_LIFE_DAYS,
  CATEGORY_LABEL,
  STATUS_CLASS,
  STATUS_LABEL,
  clamp01,
} from '@/lib/memory-style'
import type { Memory } from '@/lib/types'
import { cn } from '@/lib/utils'

export interface MemoryDetailProps {
  memoryId: string | null
  onSelect: (id: string) => void
  onMutated: () => void
}

/**
 * Everything the graph node could not say: the verbatim span it came from, the
 * edges it sits on, and the two actions that are not one-way doors.
 *
 * Provenance is the reason this panel exists. "Where did the agent get that?" is
 * the question a memory system has to be able to answer, and `source_excerpt` is
 * the answer — the exact text that produced this belief.
 */
export function MemoryDetail({ memoryId, onSelect, onMutated }: MemoryDetailProps) {
  const [busy, setBusy] = useState(false)
  const resource = useResource<Memory | null>(
    () => (memoryId ? api.memory(memoryId) : Promise.resolve(null)),
    [memoryId],
  )
  const memory = resource.data

  const act = useCallback(
    async (action: (id: string) => Promise<Memory>) => {
      if (!memory) return
      setBusy(true)
      try {
        await action(memory.id)
        resource.refresh()
        onMutated()
      } finally {
        setBusy(false)
      }
    },
    [memory, onMutated, resource],
  )

  if (!memoryId) {
    return (
      <EmptyPanel
        title="No memory selected"
        body="Click a node in the graph to see the text it was extracted from, what it replaced, and what disputes it."
      />
    )
  }
  if (resource.loading && !memory) return <Centered>Loading…</Centered>
  if (resource.error) return <EmptyPanel title="Could not load" body={resource.error} />
  if (!memory) return null

  const halfLife = CATEGORY_HALF_LIFE_DAYS[memory.category]

  return (
    <div className="scrollbar-slim h-full overflow-y-auto px-4 py-4">
      <div className="mb-3 flex flex-wrap items-center gap-1.5">
        <Badge className={cn('border', STATUS_CLASS[memory.status])}>
          {STATUS_LABEL[memory.status]}
        </Badge>
        <Badge>{CATEGORY_LABEL[memory.category]}</Badge>
        {memory.subject && <Badge>{memory.subject}</Badge>}
      </div>

      <p className="text-sm leading-relaxed text-foreground">{memory.content}</p>

      <ConfidenceBar value={memory.confidence} />

      {memory.source_excerpt && (
        <section className="mt-4">
          <SectionLabel>
            <Quote className="size-3" /> Source text
          </SectionLabel>
          <blockquote className="rounded-md border-l-2 border-accent/50 bg-surface-raised/60 px-3 py-2 text-xs leading-relaxed text-muted italic">
            “{memory.source_excerpt}”
          </blockquote>
          {memory.source_id && (
            <p className="mt-1.5 font-mono text-[10px] text-muted/70">
              source {memory.source_id}
            </p>
          )}
        </section>
      )}

      <Separator className="my-4" />

      <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
        <Stat label="Recorded" value={formatDate(memory.created_at)} />
        <Stat label="Last confirmed" value={formatDate(memory.last_reinforced_at)} />
        <Stat label="Reinforced" value={`${memory.reinforcement_count}x`} />
        <Stat label="Half-life" value={halfLife === null ? 'never decays' : `${halfLife}d`} />
      </dl>

      <EdgeList
        title="Replaced"
        hint="This belief superseded these. They are still queryable."
        ids={memory.supersedes}
        onSelect={onSelect}
      />
      <EdgeList
        title="Replaced by"
        hint="A later belief took over from this one."
        ids={memory.superseded_by ? [memory.superseded_by] : []}
        onSelect={onSelect}
      />
      <EdgeList
        title="Disputed with"
        hint="Unresolved. Both sides stay in retrieval until you decide."
        ids={memory.conflicts_with}
        onSelect={onSelect}
        tone="danger"
      />

      <div className="mt-5 flex gap-2">
        <Button
          size="sm"
          variant="secondary"
          disabled={busy}
          onClick={() => void act(api.reinforce)}
        >
          {busy ? <Loader2 className="animate-spin" /> : <ThumbsUp />}
          Still true
        </Button>
        {memory.status !== 'active' && (
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => void act(api.reactivate)}
          >
            <RotateCcw />
            Restore
          </Button>
        )}
      </div>
      <p className="mt-2 text-[11px] leading-relaxed text-muted/70">
        “Still true” raises confidence and resets the decay clock. “Restore” brings a superseded
        or archived belief back — nothing here is a one-way door.
      </p>
    </div>
  )
}

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(clamp01(value) * 100)
  return (
    <div className="mt-3">
      <div className="mb-1 flex items-baseline justify-between text-[11px] text-muted">
        <span>Confidence</span>
        <span className="tabular-nums text-foreground">{value.toFixed(2)}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-surface-raised">
        <div
          className={cn(
            'h-full rounded-full transition-all',
            pct >= 50 ? 'bg-accent' : 'bg-danger',
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

function EdgeList({
  title,
  hint,
  ids,
  onSelect,
  tone,
}: {
  title: string
  hint: string
  ids: string[]
  onSelect: (id: string) => void
  tone?: 'danger'
}) {
  if (ids.length === 0) return null
  return (
    <section className="mt-4">
      <SectionLabel>{title}</SectionLabel>
      <p className="mb-1.5 text-[11px] leading-relaxed text-muted/80">{hint}</p>
      <ul className="space-y-1">
        {ids.map((id) => (
          <li key={id}>
            <button
              type="button"
              onClick={() => onSelect(id)}
              className={cn(
                'flex w-full items-center gap-1.5 rounded border px-2 py-1 text-left font-mono text-[11px]',
                'transition-colors hover:bg-surface-raised',
                tone === 'danger'
                  ? 'border-danger/30 text-danger/90'
                  : 'border-border text-muted',
              )}
            >
              <ArrowUpRight className="size-3 shrink-0" />
              {id.slice(0, 8)}…
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <h4 className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold tracking-wide text-muted uppercase">
      {children}
    </h4>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-muted">{label}</dt>
      <dd className="text-foreground">{value}</dd>
    </div>
  )
}

export function EmptyPanel({ title, body }: { title: string; body: string }) {
  return (
    <div className="flex h-full items-center justify-center px-6 text-center">
      <div className="max-w-xs">
        <p className="mb-1 text-sm font-medium text-foreground">{title}</p>
        <p className="text-xs leading-relaxed text-muted">{body}</p>
      </div>
    </div>
  )
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="flex h-full items-center justify-center text-xs text-muted">{children}</div>
}

function formatDate(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime())
    ? '—'
    : date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}
