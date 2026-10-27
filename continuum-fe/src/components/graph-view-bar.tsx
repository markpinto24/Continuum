import { Boxes, ListCollapse } from 'lucide-react'

import { CATEGORY_LABEL } from '@/lib/memory-style'
import type { MemoryCategory } from '@/lib/types'
import { cn } from '@/lib/utils'

export type Grouping = 'auto' | 'on' | 'off'

const SINCE: { value: number | null; label: string }[] = [
  { value: null, label: 'All time' },
  { value: 7, label: 'Last 7 days' },
  { value: 30, label: 'Last 30 days' },
  { value: 90, label: 'Last 90 days' },
  { value: 365, label: 'Last year' },
]

/**
 * What the graph shows: by category, by age, and grouped by subject or not. One
 * row over the graph, native selects so keyboard and screen readers need nothing
 * special. Statuses are filtered from the legend, where their colours are.
 */
export function GraphViewBar({
  category,
  onCategory,
  categories,
  sinceDays,
  onSinceDays,
  grouping,
  grouped,
  onGrouping,
  shown,
  total,
  pulledIn,
  expandedCount,
  onCollapseAll,
  truncated = false,
}: {
  category: MemoryCategory | 'all'
  onCategory: (category: MemoryCategory | 'all') => void
  /** The categories present in this graph, so the list offers nothing empty. */
  categories: MemoryCategory[]
  sinceDays: number | null
  onSinceDays: (days: number | null) => void
  grouping: Grouping
  /** Whether grouping is in effect now (auto turns it on for big graphs). */
  grouped: boolean
  onGrouping: (grouping: Grouping) => void
  shown: number
  total: number
  pulledIn: number
  expandedCount: number
  onCollapseAll: () => void
  /** The API sent only part of the graph. */
  truncated?: boolean
}) {
  const select =
    'rounded-md border border-border bg-surface/80 px-1.5 py-1 text-xs text-foreground focus-visible:outline-2 focus-visible:outline-accent'
  return (
    <div
      role="toolbar"
      aria-label="What the graph shows"
      className="glass pointer-events-auto flex max-w-full flex-wrap items-center gap-1.5 rounded-xl px-2 py-1.5 text-xs shadow-xl shadow-black/30"
    >
      <label className="sr-only" htmlFor="graph-category">
        Category
      </label>
      <select
        id="graph-category"
        className={select}
        value={category}
        onChange={(e) => onCategory(e.target.value as MemoryCategory | 'all')}
      >
        <option value="all">All kinds</option>
        {categories.map((c) => (
          <option key={c} value={c}>
            {CATEGORY_LABEL[c]}
          </option>
        ))}
      </select>
      <label className="sr-only" htmlFor="graph-since">
        Recorded
      </label>
      <select
        id="graph-since"
        className={select}
        value={sinceDays ?? ''}
        onChange={(e) => onSinceDays(e.target.value === '' ? null : Number(e.target.value))}
      >
        {SINCE.map((s) => (
          <option key={s.label} value={s.value ?? ''}>
            {s.label}
          </option>
        ))}
      </select>
      <button
        type="button"
        aria-pressed={grouped}
        title={
          grouping === 'auto'
            ? 'Grouped automatically above 300 memories'
            : 'Group memories by subject'
        }
        onClick={() => onGrouping(grouped ? 'off' : 'on')}
        className={cn(
          'flex items-center gap-1 rounded-md border px-1.5 py-1',
          grouped
            ? 'border-accent/40 bg-accent/15 text-accent'
            : 'border-border text-muted hover:text-foreground',
        )}
      >
        <Boxes className="size-3.5" />
        By subject
      </button>
      {grouped && expandedCount > 0 && (
        <button
          type="button"
          onClick={onCollapseAll}
          className="flex items-center gap-1 rounded-md px-1.5 py-1 text-muted hover:text-foreground"
        >
          <ListCollapse className="size-3.5" />
          Regroup
        </button>
      )}
      <span className="px-1 tabular-nums text-muted" aria-live="polite">
        {shown === total ? `${total} memories` : `${shown} of ${total}`}
        {pulledIn > 0 && ` · ${pulledIn} kept: the other side of a dispute`}
      </span>
      {truncated && (
        <span className="px-1 text-amber-200">
          Only the first {total} are loaded — the graph is larger
        </span>
      )}
    </div>
  )
}
