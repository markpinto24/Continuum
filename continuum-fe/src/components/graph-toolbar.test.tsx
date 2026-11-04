import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { GraphToolbar } from '@/components/graph-toolbar'
import type { MemoryStatus } from '@/lib/types'

const COUNTS: Record<MemoryStatus, number> = {
  active: 6,
  contradicted: 2,
  pending: 0,
  superseded: 1,
  archived: 0,
  declined: 0,
}

function bar(hidden: MemoryStatus[] = [], onToggle = vi.fn()) {
  render(
    <GraphToolbar
      counts={COUNTS}
      hidden={new Set(hidden)}
      onToggle={onToggle}
      category="all"
      onCategory={() => {}}
      categories={['decision', 'fact']}
      sinceDays={null}
      onSinceDays={() => {}}
      grouping="auto"
      grouped={false}
      onGrouping={() => {}}
      shown={9}
      total={9}
      pulledIn={0}
      expandedCount={0}
      onCollapseAll={() => {}}
    />,
  )
  return onToggle
}

describe('the graph toolbar', () => {
  it('shows a chip for each status the graph has, and none for empty ones', () => {
    bar()
    expect(screen.getByRole('button', { name: /^Active: 6/ })).toBeDefined()
    expect(screen.getByRole('button', { name: /^Disputed: 2/ })).toBeDefined()
    expect(screen.queryByRole('button', { name: /^Archived/ })).toBeNull()
  })

  it('keeps a hidden status on the bar, so it can be brought back', () => {
    const onToggle = bar(['active'])
    const chip = screen.getByRole('button', { name: /^Active: 6\. Hidden/ })
    expect(chip.getAttribute('aria-pressed')).toBe('false')
    fireEvent.click(chip)
    expect(onToggle).toHaveBeenCalledWith('active')
  })

  it('keeps the key to the drawing one click away, and closes it with Escape', () => {
    bar()
    expect(screen.queryByRole('dialog', { name: 'How to read the graph' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'How to read the graph' }))
    expect(screen.getByRole('dialog', { name: 'How to read the graph' }).textContent).toMatch(/newer replaced older/)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: 'How to read the graph' })).toBeNull()
  })
})
