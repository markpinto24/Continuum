import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { GraphList } from '@/components/graph-list'
import type { ViewNode } from '@/lib/graph-view'

function node(id: string, change: Partial<ViewNode> = {}): ViewNode {
  return {
    id, label: id, category: 'decision', status: 'active', confidence: 0.7, subject: 'atlas',
    created_at: '2026-10-20T10:00:00Z', shared: false, shared_by_email: null, kind: 'fact',
    members: 1, ...change,
  }
}

const NODES = [
  node('Atlas uses Postgres'),
  node('Atlas uses MongoDB', { status: 'contradicted', pulledIn: true }),
  node('cluster:billing', { label: 'billing · 12 memories', members: 12 }),
]

describe('the graph as a list', () => {
  it('names every memory by what it says and how it stands, disputes first', () => {
    render(<GraphList nodes={NODES} onSelect={() => {}} />)
    const names = screen.getAllByRole('button').map((b) => b.textContent)
    expect(names[0]).toBe(
      'Atlas uses MongoDB — Disputed, confidence 0.70, shown because it disputes one in view',
    )
    expect(names).toContain('billing · 12 memories — a group; opens into its memories')
  })

  it('is one tab stop: arrows move within it, Enter opens', async () => {
    const onSelect = vi.fn()
    render(
      <>
        <button type="button">before</button>
        <GraphList nodes={NODES} onSelect={onSelect} />
        <button type="button">after</button>
      </>,
    )
    await userEvent.click(screen.getByText('before'))
    await userEvent.tab()
    expect(document.activeElement?.textContent).toMatch(/^Atlas uses MongoDB/)
    await userEvent.keyboard('{ArrowDown}')
    expect(document.activeElement?.textContent).toMatch(/^Atlas uses Postgres/)
    await userEvent.keyboard('{Enter}')
    expect(onSelect).toHaveBeenCalledWith('Atlas uses Postgres')
    // One more Tab leaves the list entirely — not three.
    await userEvent.tab()
    expect(document.activeElement?.textContent).toBe('after')
  })

  it('shows itself while it has focus, so a keyboard user sees where they are', async () => {
    render(<GraphList nodes={NODES} onSelect={() => {}} />)
    const nav = screen.getByRole('navigation', { name: 'Memories in the graph' })
    expect(nav.className).toBe('sr-only')
    await userEvent.tab()
    expect(nav.className).toContain('glass')
  })
})
