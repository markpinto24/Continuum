import { type KeyboardEvent, useRef, useState } from 'react'

import type { ViewNode } from '@/lib/graph-view'
import { STATUS_LABEL } from '@/lib/memory-style'
import type { MemoryStatus } from '@/lib/types'

/** Disputes first: the one status that wants something from the reader. */
const ORDER: Record<MemoryStatus, number> = {
  contradicted: 0,
  pending: 1,
  active: 2,
  superseded: 3,
  archived: 4,
  declined: 5,
}

/**
 * The graph, for anyone who cannot use the canvas: every memory in view as a
 * button, disputes first. Hidden until it has focus — then it shows, so a
 * keyboard user sees where they are. One tab stop: arrows move within it, Enter
 * opens the memory (or a subject's group), Escape leaves.
 */
export function GraphList({
  nodes,
  onSelect,
}: {
  nodes: ViewNode[]
  onSelect: (id: string) => void
}) {
  const [active, setActive] = useState(0)
  // Shown while focus is inside it, so a keyboard user sees where they are.
  const [shown, setShown] = useState(false)
  const items = useRef<(HTMLButtonElement | null)[]>([])
  const sorted = [...nodes].sort(
    (a, b) => ORDER[a.status] - ORDER[b.status] || a.label.localeCompare(b.label),
  )
  const current = Math.min(active, Math.max(0, sorted.length - 1))

  const move = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const next = {
      ArrowDown: index + 1,
      ArrowUp: index - 1,
      Home: 0,
      End: sorted.length - 1,
    }[event.key]
    if (event.key === 'Escape') {
      event.currentTarget.blur()
      return
    }
    if (next === undefined) return
    event.preventDefault()
    const clamped = Math.max(0, Math.min(sorted.length - 1, next))
    setActive(clamped)
    items.current[clamped]?.focus()
  }

  if (sorted.length === 0) return null
  return (
    <nav
      aria-label="Memories in the graph"
      onFocus={() => setShown(true)}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setShown(false)
      }}
      className={
        shown
          ? 'glass pointer-events-auto absolute top-4 left-4 z-30 max-h-[60%] w-80 max-w-[calc(100%-2rem)] overflow-y-auto rounded-xl p-2 text-xs'
          : 'sr-only'
      }
    >
      <p className="px-1.5 pb-1 text-[11px] text-muted">
        {sorted.length} in view · arrows to move, Enter to open, Escape to leave
      </p>
      <ul>
        {sorted.map((node, index) => (
          <li key={node.id}>
            <button
              ref={(el) => {
                items.current[index] = el
              }}
              type="button"
              tabIndex={index === current ? 0 : -1}
              onFocus={() => setActive(index)}
              onKeyDown={(event) => move(event, index)}
              onClick={() => onSelect(node.id)}
              className="w-full rounded-md px-1.5 py-1 text-left hover:bg-surface-raised/60 focus-visible:outline-2 focus-visible:outline-accent"
            >
              {describe(node)}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  )
}

function describe(node: ViewNode): string {
  if (node.members > 1) {
    const disputed = node.status === 'contradicted' ? ', holds a dispute' : ''
    return `${node.label} — a group${disputed}; opens into its memories`
  }
  const pulled = node.pulledIn ? ', shown because it disputes one in view' : ''
  return `${node.label} — ${STATUS_LABEL[node.status]}, confidence ${node.confidence.toFixed(2)}${pulled}`
}
