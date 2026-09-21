import { ArrowRight, Sparkles } from 'lucide-react'

import { Tooltip } from '@/components/ui/tooltip'
import { EDGE_COLOR, STATUS_BLURB, STATUS_COLOR, STATUS_LABEL } from '@/lib/memory-style'
import type { MemoryStatus } from '@/lib/types'

const ORDER: MemoryStatus[] = ['active', 'contradicted', 'superseded', 'archived']

/** Overlay explaining the encoding. A graph whose colours need a manual is a failed graph. */
export function GraphLegend({ counts }: { counts: Record<MemoryStatus, number> }) {
  return (
    <div className="pointer-events-auto absolute bottom-4 left-4 w-60 rounded-panel border border-border/70 bg-surface/80 p-3 text-xs backdrop-blur">
      <p className="mb-2 font-medium text-foreground">Belief graph</p>

      <ul className="space-y-1.5">
        {ORDER.map((status) => (
          <li key={status}>
            <Tooltip label={STATUS_BLURB[status]} side="right">
              <div className="flex cursor-help items-center gap-2">
                <span
                  className="size-2.5 shrink-0 rounded-full"
                  style={{ backgroundColor: STATUS_COLOR[status] }}
                />
                <span className="flex-1 text-muted">{STATUS_LABEL[status]}</span>
                <span className="tabular-nums text-muted/70">{counts[status] ?? 0}</span>
              </div>
            </Tooltip>
          </li>
        ))}
      </ul>

      <div className="my-2.5 h-px bg-border" />

      <ul className="space-y-1.5 text-muted">
        <li className="flex items-center gap-2">
          <ArrowRight className="size-3.5" style={{ color: EDGE_COLOR.supersedes }} />
          <span>
            <span className="text-foreground">supersedes</span> — new → old
          </span>
        </li>
        <li className="flex items-center gap-2">
          <Sparkles className="size-3.5" style={{ color: EDGE_COLOR.conflicts_with }} />
          <span>
            <span className="text-foreground">disputed</span> — awaiting you
          </span>
        </li>
        <li className="flex items-center gap-2">
          <span className="flex w-3.5 justify-center">
            <span className="size-2.5 rounded-full bg-muted/60" />
          </span>
          <span>size = confidence</span>
        </li>
      </ul>
    </div>
  )
}
