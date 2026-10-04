import { Download, GraduationCap, Loader2, Scale, X } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { type Resource, useResource } from '@/hooks/use-resource'
import { api } from '@/lib/api'
import { SHARED_SPACE, type GateEvidence, type RuleSuggestion } from '@/lib/types'
import { cn } from '@/lib/utils'

export type EvidenceScope = 'mine' | 'team'

const DISMISSED_KEY = 'continuum.dismissed-rules'

function readDismissed(): string[] {
  try {
    const raw = localStorage.getItem(DISMISSED_KEY)
    return raw ? (JSON.parse(raw) as string[]) : []
  } catch {
    return []
  }
}

function writeDismissed(keys: string[]): void {
  try {
    localStorage.setItem(DISMISSED_KEY, JSON.stringify(keys))
  } catch {
    // A private window: the suggestion just comes back next time.
  }
}

const ruleKey = (s: { owner: string; subject: string }) => `${s.owner}/${s.subject}`

/**
 * What the system has learned from the conflicts you settled.
 *
 * Every decision is kept as a labelled example, next to what the resolver had
 * thought. This panel reports the one question that matters most — would a
 * lower auto-retire bar have been safe? — plus how well the judge's confidence
 * matches people's answers, and rules worth approving. It recommends; it never
 * changes the bar, or adds a rule, by itself.
 */
export function LearningPanel({
  evidence,
  scope = 'mine',
  onScope,
  isAdmin = false,
  version,
}: {
  evidence: Resource<GateEvidence>
  scope?: EvidenceScope
  onScope?: (scope: EvidenceScope) => void
  isAdmin?: boolean
  /** Anything that changes when a decision is made: refetches the rest. */
  version?: unknown
}) {
  const data = evidence.data
  const rules = useResource(() => api.rules(), [version])
  const calibration = useResource(() => api.calibration(), [version])
  const taught = useResource(() => api.feedbackSummary(), [version])
  const [dismissed, setDismissed] = useState<string[]>(readDismissed)
  const [busy, setBusy] = useState<string | null>(null)
  const [ruleError, setRuleError] = useState<string | null>(null)

  if (!data) return null

  const act = async (key: string, run: () => Promise<unknown>) => {
    setBusy(key)
    setRuleError(null)
    try {
      await run()
      rules.refresh()
    } catch (cause) {
      setRuleError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(null)
    }
  }

  const dismiss = (suggestion: RuleSuggestion) => {
    const next = [...dismissed, ruleKey(suggestion)]
    setDismissed(next)
    writeDismissed(next)
  }

  const suggestions = (rules.data?.suggestions ?? []).filter(
    (s) => !dismissed.includes(ruleKey(s)),
  )
  const approved = rules.data?.rules ?? []
  const counts = taught.data
  const curve = calibration.data
  const bands = curve?.points.filter((p) => p.total > 0) ?? []

  return (
    <section className="mt-4 space-y-2.5 rounded-md border border-border bg-surface/60 px-3 py-2.5">
      <div className="flex items-center gap-1.5">
        <h3 className="flex items-center gap-1.5 text-[11px] font-medium text-foreground">
          <GraduationCap className="size-3.5 text-accent" />
          Learning from your decisions
        </h3>
        {onScope && (
          <div className="ml-auto flex rounded border border-border text-[10px]" role="group">
            {(['mine', 'team'] as const).map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={scope === value}
                onClick={() => onScope(value)}
                className={cn(
                  'px-1.5 py-0.5',
                  scope === value ? 'bg-surface-raised text-foreground' : 'text-muted',
                )}
              >
                {value === 'mine' ? 'Mine' : 'Everyone'}
              </button>
            ))}
          </div>
        )}
      </div>

      {data.labels === 0 ? (
        <p className="text-[11px] leading-relaxed text-muted">
          Each conflict you settle is kept as a labelled example, next to what the resolver
          thought. Over time that shows whether it can be trusted to act alone more often.
        </p>
      ) : (
        <div>
          <p className="text-[11px] leading-relaxed text-muted">
            {data.labels} {data.labels === 1 ? 'decision' : 'decisions'} recorded
            {scope === 'team' && ' across everyone'}
            {data.below_gate_total > 0 &&
              ` · ${data.below_gate_total - data.below_gate_refuted} of ${data.below_gate_total} held-back retirements confirmed`}
            {data.conflicts_both_hold > 0 &&
              ` · ${data.conflicts_both_hold} “conflicts” were both true`}
            .
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-foreground/90">{data.recommendation}</p>
        </div>
      )}

      {(approved.length > 0 || suggestions.length > 0) && (
        <div className="space-y-1.5 border-t border-border pt-2">
          <p className="text-[11px] font-medium text-foreground">Rules</p>
          {suggestions.map((s) => {
            const team = s.owner === SHARED_SPACE
            const key = ruleKey(s)
            return (
              <div key={key} className="rounded border border-border px-2 py-1.5">
                <p className="text-[11px] leading-relaxed text-muted">
                  {team ? 'The team has' : 'You have'} said both are true about{' '}
                  <span className="text-foreground">“{s.subject}”</span> {s.both_hold} times. Store
                  such pairs side by side from now on, instead of asking?
                </p>
                <div className="mt-1 flex items-center gap-1.5">
                  {team && !isAdmin ? (
                    <span className="text-[10px] text-muted/70">An admin can apply team rules.</span>
                  ) : (
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={busy === key}
                      onClick={() =>
                        void act(key, () => api.approveRule(s.subject, team ? 'team' : 'mine'))
                      }
                    >
                      {busy === key ? <Loader2 className="animate-spin" /> : <Scale />}
                      Apply rule
                    </Button>
                  )}
                  <Button size="sm" variant="ghost" onClick={() => dismiss(s)}>
                    Dismiss
                  </Button>
                </div>
              </div>
            )
          })}
          {approved.map((rule) => {
            const team = rule.owner === SHARED_SPACE
            return (
              <div key={rule.id} className="flex items-center gap-1.5 text-[11px] text-muted">
                <Scale className="size-3 shrink-0 text-accent" />
                <span>
                  {team ? 'Team: ' : ''}statements about{' '}
                  <span className="text-foreground">“{rule.subject}”</span> can all hold
                </span>
                {(!team || isAdmin) && (
                  <button
                    type="button"
                    aria-label={`Revoke the rule for ${rule.subject}`}
                    disabled={busy === rule.id}
                    onClick={() => void act(rule.id, () => api.revokeRule(rule.id))}
                    className="ml-auto text-muted/70 hover:text-foreground"
                  >
                    <X className="size-3" />
                  </button>
                )}
              </div>
            )
          })}
          {ruleError && <p className="text-[11px] text-danger">{ruleError}</p>}
        </div>
      )}

      {curve && curve.labels > 0 && (
        <details className="border-t border-border pt-2 text-[11px] text-muted">
          <summary className="cursor-pointer text-foreground">
            Judge calibration · {curve.labels} of {curve.min_labels} decisions needed
            {curve.in_use ? ' · in use' : curve.usable ? ' · ready (off)' : ''}
          </summary>
          <p className="mt-1 leading-relaxed">
            When the judge was this sure a belief had been replaced, how often you agreed. With
            <code className="mx-1">CALIBRATED_GATE</code>on, the gate uses the right-hand number.
          </p>
          <ul className="mt-1 space-y-0.5 tabular-nums">
            {bands.map((p) => (
              <li key={p.low}>
                {p.low.toFixed(1)}–{p.high.toFixed(1)}: {p.confirmed} of {p.total} right → {p.calibrated.toFixed(2)}
              </li>
            ))}
          </ul>
        </details>
      )}

      {counts && (counts.rejected_facts > 0 || counts.answers_up + counts.answers_down > 0) && (
        <p className="border-t border-border pt-2 text-[11px] text-muted">
          You have also rejected {counts.rejected_facts}{' '}
          {counts.rejected_facts === 1 ? 'misread fact' : 'misread facts'} and rated{' '}
          {counts.answers_up + counts.answers_down} answers ({counts.answers_up} up,{' '}
          {counts.answers_down} down)
          {counts.missing_memories > 0 &&
            `, naming ${counts.missing_memories} ${counts.missing_memories === 1 ? 'memory' : 'memories'} that should have come up`}
          .
        </p>
      )}

      {data.labels > 0 && (
        <a
          href={api.feedbackExportUrl}
          download
          className="inline-flex items-center gap-1 text-[11px] text-accent hover:underline"
        >
          <Download className="size-3" />
          Export as test cases
        </a>
      )}
      {counts && (counts.rejected_facts > 0 || counts.missing_memories > 0) && (
        <div className="flex flex-wrap gap-x-3 gap-y-1">
          {counts.rejected_facts > 0 && (
            <a
              href={api.feedbackExportUrlFor('extraction')}
              download
              className="inline-flex items-center gap-1 text-[11px] text-accent hover:underline"
            >
              <Download className="size-3" />
              Rejected facts as extraction cases
            </a>
          )}
          {counts.missing_memories > 0 && (
            <a
              href={api.feedbackExportUrlFor('retrieval')}
              download
              className="inline-flex items-center gap-1 text-[11px] text-accent hover:underline"
            >
              <Download className="size-3" />
              Missed memories as retrieval cases
            </a>
          )}
        </div>
      )}
    </section>
  )
}
