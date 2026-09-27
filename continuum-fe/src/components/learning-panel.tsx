import { Download, GraduationCap } from 'lucide-react'

import type { Resource } from '@/hooks/use-resource'
import { api } from '@/lib/api'
import type { GateEvidence } from '@/lib/types'

/**
 * What the system has learned from the conflicts you settled.
 *
 * Every decision is kept as a labelled example, next to what the resolver had
 * thought. This panel reports the one question that matters most — would a
 * lower auto-retire bar have been safe? — and offers the decisions as test
 * cases. It recommends; it never changes the bar by itself.
 */
export function LearningPanel({ evidence }: { evidence: Resource<GateEvidence> }) {
  const data = evidence.data
  if (!data) return null

  return (
    <section className="mt-4 rounded-md border border-border bg-surface/60 px-3 py-2.5">
      <h3 className="flex items-center gap-1.5 text-[11px] font-medium text-foreground">
        <GraduationCap className="size-3.5 text-accent" />
        Learning from your decisions
      </h3>
      {data.labels === 0 ? (
        <p className="mt-1 text-[11px] leading-relaxed text-muted">
          Each conflict you settle is kept as a labelled example, next to what the resolver
          thought. Over time that shows whether it can be trusted to act alone more often.
        </p>
      ) : (
        <>
          <p className="mt-1 text-[11px] leading-relaxed text-muted">
            {data.labels} {data.labels === 1 ? 'decision' : 'decisions'} recorded
            {data.below_gate_total > 0 &&
              ` · ${data.below_gate_total - data.below_gate_refuted} of ${data.below_gate_total} held-back retirements confirmed`}
            {data.conflicts_both_hold > 0 &&
              ` · ${data.conflicts_both_hold} “conflicts” were both true`}
            .
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-foreground/90">{data.recommendation}</p>
          <a
            href={api.feedbackExportUrl}
            download
            className="mt-1.5 inline-flex items-center gap-1 text-[11px] text-accent hover:underline"
          >
            <Download className="size-3" />
            Export as test cases
          </a>
        </>
      )}
    </section>
  )
}
