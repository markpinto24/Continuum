import { Check, Loader2, Scale, Share2, Users } from 'lucide-react'
import { useState } from 'react'

import { EmptyPanel } from '@/components/memory-detail'
import { Badge } from '@/components/ui/badge'
import { type EvidenceScope, LearningPanel } from '@/components/learning-panel'
import { Button } from '@/components/ui/button'
import { PendingQuestionCard } from '@/components/turn-outcome'
import { questionFor } from '@/lib/pending'
import { api } from '@/lib/api'
import { describeEscalation, findEscalation } from '@/lib/escalation'
import { CATEGORY_LABEL } from '@/lib/memory-style'
import { SHARED_SPACE, type ConflictPair, type Memory, type TeamDecision } from '@/lib/types'
import { type Resource, useResource } from '@/hooks/use-resource'
import { cn } from '@/lib/utils'
import type { ConflictListResponse, Mode } from '@/lib/types'

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
  isAdmin = false,
  mode = 'workspace',
}: {
  resource: Resource<ConflictListResponse>
  onResolved: () => void
  onSelect: (id: string) => void
  isAdmin?: boolean
  /** Personal has no team: its evidence and rules are yours alone. */
  mode?: Mode
}) {
  const [pending, setPending] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [scope, setScope] = useState<EvidenceScope>('mine')
  // Disputes are two beliefs at odds; undecided items are things you said
  // "maybe" to. Different questions, so different lists (requirements.md §13).
  const [view, setView] = useState<'disputes' | 'undecided'>('disputes')
  const undecided = useResource(() => api.pendingMemories(), [resource.data])
  const [answered, setAnswered] = useState<Record<string, string>>({})
  // Refetched whenever the conflict list reloads — i.e. after any decision,
  // whether made here or in the chat.
  const evidenceScope = mode === 'workspace' ? scope : 'mine'
  const evidence = useResource(
    () => api.feedbackEvidence(evidenceScope),
    [resource.data, evidenceScope],
  )
  const learning = (
    <LearningPanel
      evidence={evidence}
      scope={mode === 'workspace' ? scope : 'mine'}
      onScope={mode === 'workspace' ? setScope : undefined}
      isAdmin={isAdmin}
      version={resource.data}
      mode={mode}
    />
  )

  const settleTeam = async (pair: ConflictPair, decision: TeamDecision) => {
    const team = pair.conflicting[0]
    if (!team) return
    setPending(pair.memory.id)
    setError(null)
    try {
      await api.resolveTeamConflict(pair.memory.id, team.id, decision)
      resource.refresh()
      onResolved()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setPending(null)
    }
  }

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

  const disputeCount = resource.data?.total ?? 0
  const undecidedItems = undecided.data?.memories ?? []
  const header = (
    <div role="tablist" aria-label="Inbox" className="mb-3 flex gap-1 rounded-xl bg-surface p-0.5 ring-1 ring-border/60">
      {(
        [
          ['disputes', 'Disputes', disputeCount],
          ['undecided', 'Undecided', undecidedItems.length],
        ] as const
      ).map(([value, label, count]) => (
        <button
          key={value}
          type="button"
          role="tab"
          aria-selected={view === value}
          onClick={() => setView(value)}
          className={cn(
            'flex h-7 flex-1 items-center justify-center gap-1.5 rounded-[0.6rem] text-xs transition-colors',
            view === value ? 'bg-surface-raised text-foreground' : 'text-muted hover:text-foreground',
          )}
        >
          {label}
          {count > 0 && <span className="tabular-nums text-muted">{count}</span>}
        </button>
      ))}
    </div>
  )

  if (view === 'undecided') {
    return (
      <div className="scrollbar-slim h-full overflow-y-auto px-4 py-4">
        {header}
        {undecidedItems.length === 0 ? (
          <EmptyPanel
            title="Nothing undecided"
            body="When you say “maybe” or “I'll think about it”, it is kept here — never treated as true — and you are asked when it comes up. Asked three times with no answer, it fades."
          />
        ) : (
          <ul className="space-y-2.5">
            {undecidedItems.map((memory) => (
              <li key={memory.id}>
                <PendingQuestionCard
                  question={questionFor(memory)}
                  outcome={answered[memory.id]}
                  onAnswered={(outcome) => {
                    setAnswered((was) => ({ ...was, [memory.id]: outcome }))
                    onResolved()
                  }}
                />
              </li>
            ))}
          </ul>
        )}
      </div>
    )
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
      <div className="scrollbar-slim h-full overflow-y-auto px-4 py-4">
        {header}
        <EmptyPanel
          title="Nothing to decide"
          body="No unresolved contradictions. When the resolver is not confident enough to retire a belief on its own, the pair lands here instead of being guessed at."
        />
        {learning}
      </div>
    )
  }

  return (
    <div className="scrollbar-slim h-full overflow-y-auto px-4 py-4">
      {header}
      <p className="mb-3 text-xs leading-relaxed text-muted">
        {pairs.length} unresolved {pairs.length === 1 ? 'disagreement' : 'disagreements'}. The
        system stopped rather than pick a side.
      </p>

      {error && <p className="mb-2 text-[11px] text-danger">{error}</p>}
      <ul className="space-y-3">
        {pairs.map((pair) => {
          const busy = pending === pair.memory.id
          if (pair.team) {
            return (
              <TeamDispute
                key={`team-${pair.memory.id}`}
                pair={pair}
                busy={busy}
                onSelect={onSelect}
                onSettle={(decision) => void settleTeam(pair, decision)}
              />
            )
          }
          const sides = [pair.memory, ...pair.conflicting]
          const why = describeEscalation(findEscalation(sides))

          return (
            <li key={pair.memory.id} className="panel overflow-hidden">
              {pair.memory.user_id === SHARED_SPACE && (
                <p className="border-b border-border bg-sky-500/10 px-3 py-1.5 text-[11px] text-sky-300">
                  Team knowledge — anyone on this Continuum can settle it.
                </p>
              )}
              {why && (
                <p className="border-b border-border bg-surface-raised/40 px-3 py-1.5 text-[11px] leading-relaxed text-muted">
                  {why}
                </p>
              )}
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
      {learning}
    </div>
  )
}

/**
 * Your private belief against the team's. Only yours can change from here:
 * if you think the team is out of date, sharing yours lets the team graph's own
 * resolver — or the team — decide.
 */
function TeamDispute({
  pair,
  busy,
  onSelect,
  onSettle,
}: {
  pair: ConflictPair
  busy: boolean
  onSelect: (id: string) => void
  onSettle: (decision: TeamDecision) => void
}) {
  const team = pair.conflicting[0]
  if (!team) return null
  const why = describeEscalation(findEscalation([pair.memory, team]))
  return (
    <li className="panel overflow-hidden">
      <p className="flex items-center gap-1.5 border-b border-border bg-sky-500/10 px-3 py-1.5 text-[11px] text-sky-300">
        <Users className="size-3" />
        Your note disagrees with what the team holds. Only you see this.
      </p>
      {why && (
        <p className="border-b border-border bg-surface-raised/40 px-3 py-1.5 text-[11px] leading-relaxed text-muted">
          {why}
        </p>
      )}
      <div className="divide-y divide-border">
        <TeamSide label="Yours" memory={pair.memory} onInspect={() => onSelect(pair.memory.id)} />
        <TeamSide
          label={team.shared_by_email ? `Team · shared by ${team.shared_by_email}` : 'Team'}
          memory={team}
          onInspect={() => onSelect(team.id)}
        />
      </div>
      <div className="flex flex-wrap items-center gap-1.5 border-t border-border bg-surface-raised/40 px-3 py-2">
        <Button size="sm" variant="secondary" disabled={busy} onClick={() => onSettle('team_holds')}>
          {busy ? <Loader2 className="animate-spin" /> : <Check />}
          The team is right
        </Button>
        <Button size="sm" variant="secondary" disabled={busy} onClick={() => onSettle('mine_holds')}>
          <Share2 />
          Mine holds — share it
        </Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => onSettle('both_hold')}>
          <Scale />
          Both are true
        </Button>
      </div>
    </li>
  )
}

function TeamSide({
  label,
  memory,
  onInspect,
}: {
  label: string
  memory: Memory
  onInspect: () => void
}) {
  return (
    <div className="px-3 py-2.5">
      <div className="mb-1.5 flex items-center gap-1.5">
        <Badge>{label}</Badge>
        <Badge>{CATEGORY_LABEL[memory.category]}</Badge>
        <span className="ml-auto text-[10px] text-muted/70">
          {new Date(memory.created_at).toLocaleDateString()}
        </span>
      </div>
      <p className="text-xs leading-relaxed text-foreground">{memory.content}</p>
      <Button size="sm" variant="ghost" className="mt-1.5" onClick={onInspect}>
        Inspect
      </Button>
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
