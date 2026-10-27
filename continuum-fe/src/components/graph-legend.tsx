import { ArrowRight, ChevronDown, Sparkles } from 'lucide-react'
import { useState } from 'react'

import { Tooltip } from '@/components/ui/tooltip'
import { EDGE_COLOR, STATUS_BLURB, STATUS_COLOR, STATUS_LABEL, SUMMARY_COLOR } from '@/lib/memory-style'
import type { MemoryStatus } from '@/lib/types'
import { cn } from '@/lib/utils'

const ORDER: MemoryStatus[] = ['active', 'contradicted', 'pending', 'superseded', 'archived']
const OPEN_KEY = 'continuum.legend_open'

function readOpen(): boolean {
  try {
    return localStorage.getItem(OPEN_KEY) === '1'
  } catch {
    return false
  }
}

/**
 * Overlay explaining the encoding. A graph whose colours need a manual is a
 * failed graph — so the colours and counts are always on screen, and the rest
 * of the key folds away once you know it.
 */
export function GraphLegend({
  counts,
  hidden = new Set(),
  onToggle,
}: {
  counts: Record<MemoryStatus, number>
  /** Statuses hidden from the graph; their rows stay, dimmed, to bring them back. */
  hidden?: ReadonlySet<MemoryStatus>
  onToggle?: (status: MemoryStatus) => void
}) {
  const [open, setOpen] = useState(readOpen)
  const toggle = () => {
    setOpen((was) => {
      try {
        localStorage.setItem(OPEN_KEY, was ? '0' : '1')
      } catch {
        // A private window: the legend just forgets.
      }
      return !was
    })
  }

  return (
    <div className="glass pointer-events-auto w-56 rounded-xl p-2.5 text-xs shadow-xl shadow-black/30">
      <ul className="space-y-0.5">
        {ORDER.map((status) => (
          <li key={status}>
            <Tooltip label={STATUS_BLURB[status]} side="right">
              <button
                type="button"
                // Shown or hidden in the graph; the count is always the whole graph's.
                aria-pressed={!hidden.has(status)}
                aria-label={`${STATUS_LABEL[status]}: ${counts[status] ?? 0}. ${
                  hidden.has(status) ? 'Hidden — show in the graph' : 'Shown — hide from the graph'
                }`}
                onClick={() => onToggle?.(status)}
                disabled={!onToggle}
                className={cn(
                  'flex w-full items-center gap-2.5 rounded-md px-1.5 py-1 text-left hover:bg-surface-raised/60',
                  'focus-visible:outline-2 focus-visible:outline-accent',
                  hidden.has(status) && 'opacity-45',
                )}
              >
                <span
                  className="size-2 shrink-0 rounded-full"
                  style={{
                    backgroundColor: STATUS_COLOR[status],
                    boxShadow: hidden.has(status) ? 'none' : `0 0 8px ${STATUS_COLOR[status]}`,
                  }}
                />
                <span
                  className={cn('flex-1 text-foreground/85', hidden.has(status) && 'line-through')}
                >
                  {STATUS_LABEL[status]}
                </span>
                <span className="tabular-nums text-muted">{counts[status] ?? 0}</span>
              </button>
            </Tooltip>
          </li>
        ))}
      </ul>

      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className="mt-1 flex w-full items-center justify-between rounded-md px-1.5 py-1 text-muted transition-colors hover:text-foreground"
      >
        How to read
        <ChevronDown className={cn('size-3.5 transition-transform', open && 'rotate-180')} />
      </button>

      {open && (
        <ul className="mt-1 space-y-1.5 border-t border-border/70 px-1.5 pt-2 text-muted animate-fade-up">
          <li className="flex items-center gap-2">
            <ArrowRight className="size-3.5" style={{ color: EDGE_COLOR.supersedes }} />
            <span>
              <span className="text-foreground">arrow</span> — newer replaced older
            </span>
          </li>
          <li className="flex items-center gap-2">
            <Sparkles className="size-3.5" style={{ color: EDGE_COLOR.conflicts_with }} />
            <span>
              <span className="text-foreground">pulse</span> — a dispute for you
            </span>
          </li>
          <li className="flex items-center gap-2">
            <span className="flex w-3.5 justify-center">
              <span className="size-2.5 rounded-full bg-muted/60" />
            </span>
            <span>size = confidence</span>
          </li>
          <li className="flex items-center gap-2">
            <span className="flex w-3.5 justify-center">
              <span className="size-3 rounded-full border border-dashed border-sky-300/70" />
            </span>
            <span>
              <span className="text-foreground">halo</span> — shared with the team
            </span>
          </li>
          <li className="flex items-center gap-2">
            <span className="flex w-3.5 justify-center">
              <span className="size-3 rounded-full border-2" style={{ borderColor: SUMMARY_COLOR }} />
            </span>
            <span>
              <span className="text-foreground">ring</span> — a summary of a subject
            </span>
          </li>
        </ul>
      )}
    </div>
  )
}
