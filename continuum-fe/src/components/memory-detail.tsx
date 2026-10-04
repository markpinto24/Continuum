import {
  ArrowUpRight,
  Ban,
  EraserIcon,
  Layers,
  Loader2,
  Quote,
  RotateCcw,
  ThumbsUp,
  Users,
} from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

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
import { SHARED_SPACE, type Memory, type RejectReason, type ShareResponse } from '@/lib/types'
import { cn } from '@/lib/utils'

export interface MemoryDetailProps {
  memoryId: string | null
  onSelect: (id: string) => void
  onMutated: () => void
  /** Who is looking: decides whether corrections are offered (the server decides for real). */
  userId?: string
  isAdmin?: boolean
}

const REJECT_REASONS: { value: RejectReason; label: string }[] = [
  { value: 'not_a_fact', label: 'Not a fact (a question, a guess, chit-chat)' },
  { value: 'merged', label: 'Two separate facts joined into one' },
  { value: 'misread', label: 'The text says something else' },
  { value: 'other', label: 'Something else' },
]

/**
 * Everything the graph node could not say: the verbatim span it came from, the
 * edges it sits on, and the two actions that are not one-way doors.
 *
 * Provenance is the reason this panel exists. "Where did the agent get that?" is
 * the question a memory system has to be able to answer, and `source_excerpt` is
 * the answer — the exact text that produced this belief.
 */
const SHARE_OUTCOME: Record<ShareResponse['outcome'], string> = {
  created: 'Shared. Everyone on this Continuum can now draw on it.',
  merged: 'The team already knew this — the shared memory was confirmed instead of duplicated.',
  superseded: 'Shared, and it replaced an older team belief.',
  conflict: 'Shared, but it contradicts what the team holds. It is waiting in everyone’s inbox.',
}

export function MemoryDetail({
  memoryId,
  onSelect,
  onMutated,
  userId,
  isAdmin = false,
}: MemoryDetailProps) {
  const [busy, setBusy] = useState(false)
  const [correcting, setCorrecting] = useState<'reject' | 'forget' | null>(null)
  const [correctionNote, setCorrectionNote] = useState<string | null>(null)
  const resource = useResource<Memory | null>(
    () => (memoryId ? api.memory(memoryId) : Promise.resolve(null)),
    [memoryId],
  )
  const memory = resource.data

  const [shareNote, setShareNote] = useState<string | null>(null)
  // Reset when another memory is selected.
  useEffect(() => {
    setShareNote(null)
    setCorrecting(null)
    setCorrectionNote(null)
  }, [memoryId])

  /** Run a correction, then say what it did. */
  const correct = useCallback(
    async (run: () => Promise<string>) => {
      setBusy(true)
      try {
        setCorrectionNote(await run())
        setCorrecting(null)
        resource.refresh()
        onMutated()
      } catch (cause) {
        setCorrectionNote(cause instanceof Error ? cause.message : String(cause))
      } finally {
        setBusy(false)
      }
    },
    [onMutated, resource],
  )

  /** Share, then say what the shared graph's resolver made of it. */
  const share = useCallback(async () => {
    if (!memory) return
    setBusy(true)
    try {
      const { outcome, shared } = await api.shareMemory(memory.id)
      setShareNote(SHARE_OUTCOME[outcome])
      onMutated()
      onSelect(shared.id)
    } catch (cause) {
      setShareNote(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }, [memory, onMutated, onSelect])

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
  const forgotten = memory.redacted_at !== null
  const summary = memory.kind === 'summary'
  const mayCorrect =
    !forgotten &&
    (userId === undefined ||
      memory.user_id === userId ||
      (memory.user_id === SHARED_SPACE && (isAdmin || memory.shared_by === userId)))

  return (
    <div className="scrollbar-slim h-full overflow-y-auto px-5 py-5 animate-fade-up">
      <div className="mb-3 flex flex-wrap items-center gap-1.5">
        <Badge className={cn('border', STATUS_CLASS[memory.status])}>
          {STATUS_LABEL[memory.status]}
        </Badge>
        <Badge>{CATEGORY_LABEL[memory.category]}</Badge>
        {memory.subject && <Badge>{memory.subject}</Badge>}
        {summary && (
          <Badge className="border-violet-400/30 text-violet-300">
            <Layers className="size-3" />
            Summary
          </Badge>
        )}
        {forgotten && <Badge className="border-danger/40 text-danger">Forgotten</Badge>}
        {memory.rejected_reason && (
          <Badge className="border-danger/40 text-danger">Rejected</Badge>
        )}
        {memory.user_id === SHARED_SPACE && (
          <Badge className="border-sky-400/30 text-sky-300">
            <Users className="size-3" />
            Shared{memory.shared_by_email ? ` by ${memory.shared_by_email}` : ''}
          </Badge>
        )}
      </div>

      <p className="text-[15px] leading-relaxed font-medium text-foreground">{memory.content}</p>
      {summary && (
        <p className="mt-1 text-[11px] leading-relaxed text-muted/80">
          Written from {memory.derived_from.length} memories about this subject. A summary is never
          evidence of its own: it is rewritten when they change.
        </p>
      )}
      {forgotten && (
        <p className="mt-1 text-[11px] leading-relaxed text-muted/80">
          Forgotten on {formatDate(memory.redacted_at as string)}. Its words are gone everywhere
          Continuum kept them; the record stays so what replaced what still reads.
        </p>
      )}

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
      <EdgeList
        title="Disagrees with the team"
        hint="Settle it in the inbox: the team is right, yours holds, or both are."
        ids={memory.team_conflicts_with}
        onSelect={onSelect}
        tone="danger"
      />
      <EdgeList
        title="Written from"
        hint="The memories this summary condenses."
        ids={memory.derived_from}
        onSelect={onSelect}
      />

      <div className="mt-5 flex flex-wrap gap-2">
        {!forgotten && !summary && (
          <Button
            size="sm"
            variant="secondary"
            disabled={busy}
            onClick={() => void act(api.reinforce)}
          >
            {busy ? <Loader2 className="animate-spin" /> : <ThumbsUp />}
            Still true
          </Button>
        )}
        {memory.status !== 'active' && !memory.shared_as && !forgotten && (
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
        {memory.user_id !== SHARED_SPACE && memory.status === 'active' && !summary && (
          <Button size="sm" variant="outline" disabled={busy} onClick={() => void share()}>
            <Users />
            Share with team
          </Button>
        )}
      </div>
      {shareNote && <p className="mt-2 text-[11px] leading-relaxed text-sky-300">{shareNote}</p>}
      {memory.shared_as && (
        <button
          type="button"
          onClick={() => onSelect(memory.shared_as as string)}
          className="mt-2 text-[11px] text-sky-300 hover:underline"
        >
          Moved to the shared space →
        </button>
      )}
      <p className="mt-2 text-[11px] leading-relaxed text-muted/70">
        “Still true” raises confidence and resets the decay clock. “Restore” brings a superseded
        or archived belief back — nothing here is a one-way door.
      </p>

      {mayCorrect && (
        <section className="mt-4 border-t border-border pt-3">
          <SectionLabel>Correct it</SectionLabel>
          {correcting === null && (
            <div className="flex flex-wrap gap-2">
              {!memory.rejected_reason && (
                <Button size="sm" variant="outline" disabled={busy} onClick={() => setCorrecting('reject')}>
                  <Ban />
                  This isn’t a real fact
                </Button>
              )}
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => setCorrecting('forget')}>
                <EraserIcon />
                Forget…
              </Button>
            </div>
          )}
          {correcting === 'reject' && (
            <div className="space-y-1.5">
              <p className="text-[11px] leading-relaxed text-muted">
                What went wrong? It is archived with your reason, anything it wrongly replaced comes
                back, and the extractor is tested against it from now on.
              </p>
              {REJECT_REASONS.map(({ value, label }) => (
                <Button
                  key={value}
                  size="sm"
                  variant="secondary"
                  className="w-full justify-start"
                  disabled={busy}
                  onClick={() =>
                    void correct(async () => {
                      const { restored } = await api.rejectMemory(memory.id, value)
                      return restored.length
                        ? `Rejected. ${restored.length} belief${restored.length === 1 ? '' : 's'} it had replaced ${restored.length === 1 ? 'is' : 'are'} active again.`
                        : 'Rejected and archived.'
                    })
                  }
                >
                  {label}
                </Button>
              ))}
              <Button size="sm" variant="ghost" onClick={() => setCorrecting(null)}>
                Cancel
              </Button>
            </div>
          )}
          {correcting === 'forget' && (
            <div className="space-y-1.5 rounded-md border border-danger/40 bg-danger/5 px-3 py-2">
              <p className="text-[11px] leading-relaxed text-foreground">
                Forgetting erases what this memory says — its text, source excerpt, subject and
                search vector, and the copies in your decisions, feedback and any summary. The empty
                record stays so the graph still reads. <strong>This cannot be undone.</strong>
              </p>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="danger"
                  disabled={busy}
                  onClick={() =>
                    void correct(async () => {
                      await api.forgetMemory(memory.id)
                      return 'Forgotten.'
                    })
                  }
                >
                  {busy ? <Loader2 className="animate-spin" /> : <EraserIcon />}
                  Forget permanently
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setCorrecting(null)}>
                  Keep it
                </Button>
              </div>
            </div>
          )}
        </section>
      )}
      {correctionNote && (
        <p className="mt-2 text-[11px] leading-relaxed text-muted">{correctionNote}</p>
      )}
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
    <section className="mt-5">
      <SectionLabel>{title}</SectionLabel>
      <p className="mb-2 text-xs leading-relaxed text-muted">{hint}</p>
      <ul className="space-y-1.5">
        {ids.map((id) => (
          <li key={id}>
            <EdgeItem id={id} onSelect={onSelect} tone={tone} />
          </li>
        ))}
      </ul>
    </section>
  )
}

/** One linked memory, by what it says — an id means nothing to a person. */
function EdgeItem({
  id,
  onSelect,
  tone,
}: {
  id: string
  onSelect: (id: string) => void
  tone?: 'danger'
}) {
  const linked = useResource<Memory | null>(() => api.memory(id).catch(() => null), [id])
  const memory = linked.data
  return (
    <button
      type="button"
      onClick={() => onSelect(id)}
      className={cn(
        'group flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left text-xs leading-relaxed ring-1 transition-colors',
        tone === 'danger'
          ? 'bg-amber-400/[0.06] text-amber-50/90 ring-amber-400/25 hover:bg-amber-400/10'
          : 'bg-surface text-foreground/85 ring-border hover:bg-surface-raised',
      )}
    >
      <ArrowUpRight className="mt-0.5 size-3.5 shrink-0 opacity-60 transition-opacity group-hover:opacity-100" />
      <span className="min-w-0 flex-1">
        {memory ? memory.content : <span className="font-mono text-muted">{id.slice(0, 8)}…</span>}
        {memory && (
          <span className="mt-0.5 block text-[11px] text-muted">
            {STATUS_LABEL[memory.status]} · {CATEGORY_LABEL[memory.category]} ·{' '}
            {formatDate(memory.created_at)}
          </span>
        )}
      </span>
    </button>
  )
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <h4 className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold tracking-[0.08em] text-muted uppercase">
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
