import { ArrowRight, Boxes, ChevronDown, CircleHelp, ListCollapse, Sparkles, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import {
  CATEGORY_LABEL,
  EDGE_COLOR,
  STATUS_BLURB,
  STATUS_COLOR,
  STATUS_LABEL,
  SUMMARY_COLOR,
} from '@/lib/memory-style'
import type { MemoryCategory, MemoryStatus } from '@/lib/types'
import { cn } from '@/lib/utils'

export type Grouping = 'auto' | 'on' | 'off'

const ORDER: MemoryStatus[] = ['active', 'contradicted', 'pending', 'superseded', 'archived']

const SINCE: { value: number | null; label: string }[] = [
  { value: null, label: 'All time' },
  { value: 7, label: 'Last 7 days' },
  { value: 30, label: 'Last 30 days' },
  { value: 90, label: 'Last 90 days' },
  { value: 365, label: 'Last year' },
]

/**
 * What the graph shows, in one slim bar over it: the statuses as chips — colour,
 * name and count, each a toggle — then kind, age and grouping, and the key to
 * the drawing behind a "How to read" button.
 *
 * It replaced a legend card and a filter bar stacked in the top-left corner: two
 * panels, one of them tall, covering the graph they explained. A chip is shown
 * for each status the graph has, or that is hidden, so an empty one never asks
 * for attention — and a hidden one is never lost.
 */
export function GraphToolbar({
  counts,
  hidden,
  onToggle,
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
  counts: Record<MemoryStatus, number>
  /** Statuses hidden from the graph; their chips stay, struck through, to bring them back. */
  hidden: ReadonlySet<MemoryStatus>
  onToggle: (status: MemoryStatus) => void
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
  const [keyOpen, setKeyOpen] = useState(false)
  const statuses = ORDER.filter((status) => (counts[status] ?? 0) > 0 || hidden.has(status))

  return (
    <div className="pointer-events-auto relative max-w-full">
      <div
        role="toolbar"
        aria-label="What the graph shows"
        className="glass scrollbar-none flex h-10 max-w-full items-center gap-0.5 overflow-x-auto rounded-full px-1.5 text-xs shadow-xl shadow-black/30"
      >
        {statuses.map((status) => {
          const off = hidden.has(status)
          const count = counts[status] ?? 0
          return (
            <button
              key={status}
              type="button"
              // Shown or hidden in the graph; the count is always the whole graph's.
              aria-pressed={!off}
              aria-label={`${STATUS_LABEL[status]}: ${count}. ${
                off ? 'Hidden — show in the graph' : 'Shown — hide from the graph'
              }`}
              title={STATUS_BLURB[status]}
              onClick={() => onToggle(status)}
              className={cn(
                'flex h-7 shrink-0 items-center gap-1.5 rounded-full px-2.5 transition-colors hover:bg-white/[0.06]',
                'focus-visible:outline-2 focus-visible:outline-accent',
                off && 'opacity-45',
              )}
            >
              <span
                className="size-2 rounded-full"
                style={{
                  backgroundColor: STATUS_COLOR[status],
                  boxShadow: off ? 'none' : `0 0 8px ${STATUS_COLOR[status]}`,
                }}
              />
              {/* In a narrow column the chip is its colour and count; the name is
                  in its label and tooltip. */}
              <span className={cn('text-foreground/85 @max-[760px]:hidden', off && 'line-through')}>
                {STATUS_LABEL[status]}
              </span>
              <span className="tabular-nums text-muted">{count}</span>
            </button>
          )
        })}

        <Divider />

        <PillSelect
          label="Kind"
          value={category}
          onChange={(value) => onCategory(value as MemoryCategory | 'all')}
          options={[
            { value: 'all', label: 'All kinds' },
            ...categories.map((c) => ({ value: c, label: CATEGORY_LABEL[c] })),
          ]}
        />
        <PillSelect
          label="Recorded"
          value={sinceDays === null ? '' : String(sinceDays)}
          onChange={(value) => onSinceDays(value === '' ? null : Number(value))}
          options={SINCE.map((s) => ({ value: s.value === null ? '' : String(s.value), label: s.label }))}
        />
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
            'flex h-7 shrink-0 items-center gap-1.5 rounded-full px-2.5 transition-colors',
            grouped ? 'bg-accent/15 text-accent' : 'text-muted hover:bg-white/[0.06] hover:text-foreground',
          )}
        >
          <Boxes className="size-3.5" />
          <span className="@max-[760px]:sr-only">By subject</span>
        </button>
        {grouped && expandedCount > 0 && (
          <button
            type="button"
            onClick={onCollapseAll}
            className="flex h-7 shrink-0 items-center gap-1.5 rounded-full px-2.5 text-muted hover:bg-white/[0.06] hover:text-foreground"
          >
            <ListCollapse className="size-3.5" />
            Regroup
          </button>
        )}

        <Divider />

        <span
          className="shrink-0 px-1.5 whitespace-nowrap tabular-nums text-muted"
          aria-live="polite"
          title={pulledIn > 0 ? `${pulledIn} kept in view: the other side of a dispute you can see` : undefined}
        >
          {shown === total ? total : `${shown} of ${total}`}
          <span className="@max-[760px]:hidden"> memories</span>
          {pulledIn > 0 && ` · +${pulledIn}`}
        </span>
        {truncated && (
          <span
            className="shrink-0 rounded-full bg-amber-400/15 px-2 py-0.5 whitespace-nowrap text-amber-200"
            title={`Only the first ${total} are loaded — the graph is larger`}
          >
            Partial
          </span>
        )}
        <button
          type="button"
          aria-expanded={keyOpen}
          aria-label="How to read the graph"
          onClick={() => setKeyOpen((open) => !open)}
          className={cn(
            'flex size-7 shrink-0 items-center justify-center rounded-full transition-colors',
            keyOpen ? 'bg-white/10 text-foreground' : 'text-muted hover:bg-white/[0.06] hover:text-foreground',
          )}
        >
          <CircleHelp className="size-4" />
        </button>
      </div>

      {keyOpen && <ReadingKey onClose={() => setKeyOpen(false)} />}
    </div>
  )
}

function Divider() {
  return <span aria-hidden className="mx-1 h-4 w-px shrink-0 bg-white/10" />
}

/** A native select dressed as a pill: the keyboard and screen readers need nothing special. */
function PillSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  options: { value: string; label: string }[]
}) {
  return (
    <span className="relative flex shrink-0 items-center">
      <select
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-7 cursor-pointer appearance-none rounded-full bg-transparent pr-7 pl-2.5 text-xs text-foreground/85 transition-colors hover:bg-white/[0.06] focus-visible:outline-2 focus-visible:outline-accent [&>option]:bg-surface [&>option]:text-foreground"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <ChevronDown aria-hidden className="pointer-events-none absolute right-2 size-3.5 text-muted" />
    </span>
  )
}

/** The key to the drawing, under the bar while it is wanted. Escape or a click elsewhere closes it. */
function ReadingKey({ onClose }: { onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && onClose()
    const onDown = (event: PointerEvent) => {
      const panel = ref.current
      // A click on the bar itself (its own toggle included) is the bar's business.
      const bar = panel?.parentElement
      if (bar && !bar.contains(event.target as Node)) onClose()
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('pointerdown', onDown)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('pointerdown', onDown)
    }
  }, [onClose])

  return (
    <div
      ref={ref}
      role="dialog"
      aria-label="How to read the graph"
      className="glass absolute top-12 right-0 z-10 w-64 rounded-2xl p-3.5 text-xs text-muted shadow-xl shadow-black/40 animate-fade-up"
    >
      <div className="mb-2 flex items-center justify-between">
        <span className="font-medium text-foreground">How to read</span>
        <button type="button" aria-label="Close" onClick={onClose} className="rounded-full p-0.5 hover:text-foreground">
          <X className="size-3.5" />
        </button>
      </div>
      <ul className="space-y-2">
        <Row icon={<span className="size-2.5 rounded-full bg-muted/60" />}>
          <b className="font-normal text-foreground">colour</b> — status; click a chip to hide or show it
        </Row>
        <Row icon={<span className="size-2.5 rounded-full bg-muted/60" />}>
          <b className="font-normal text-foreground">size</b> — confidence
        </Row>
        <Row icon={<ArrowRight className="size-3.5" style={{ color: EDGE_COLOR.supersedes }} />}>
          <b className="font-normal text-foreground">arrow</b> — newer replaced older
        </Row>
        <Row icon={<Sparkles className="size-3.5" style={{ color: EDGE_COLOR.conflicts_with }} />}>
          <b className="font-normal text-foreground">pulse</b> — a dispute for you
        </Row>
        <Row icon={<span className="size-3 rounded-full border border-dashed border-sky-300/70" />}>
          <b className="font-normal text-foreground">halo</b> — shared with the team
        </Row>
        <Row icon={<span className="size-3 rounded-full border-2" style={{ borderColor: SUMMARY_COLOR }} />}>
          <b className="font-normal text-foreground">ring</b> — a summary of a subject
        </Row>
      </ul>
    </div>
  )
}

function Row({ icon, children }: { icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <li className="flex items-center gap-2.5">
      <span className="flex w-3.5 shrink-0 justify-center">{icon}</span>
      <span>{children}</span>
    </li>
  )
}
