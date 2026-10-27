import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ActivityPanel } from '@/components/activity-panel'
import type { ActivityDay, ActivityReport } from '@/lib/types'

const activity = vi.hoisted(() => vi.fn())
vi.mock('@/lib/api', () => ({ api: { feedbackActivity: activity } }))

function day(n: number, change: Partial<ActivityDay> = {}): ActivityDay {
  return {
    day: `2026-10-${String(n).padStart(2, '0')}`, remembered: 0, escalated: 0, auto_retired: 0,
    decided: 0, decided_by_agent: 0, judge_confidence: null, ...change,
  }
}

const REPORT: ActivityReport = {
  days: [
    day(25, { remembered: 4, escalated: 1, judge_confidence: 0.62 }),
    day(26),
    day(27, { remembered: 2, decided: 2, decided_by_agent: 1, auto_retired: 1 }),
  ],
  gate: 0.8, remembered: 6, escalated: 1, auto_retired: 1, decided: 2, escalation_rate: 1 / 6,
}

beforeEach(() => {
  activity.mockReset().mockResolvedValue(REPORT)
  // jsdom has no PointerEvent; a MouseEvent carries the clientX the chart reads.
  if (!('PointerEvent' in window)) {
    Object.defineProperty(window, 'PointerEvent', { value: MouseEvent, configurable: true })
  }
})

describe('the activity dashboard', () => {
  it('leads with the numbers that matter', async () => {
    render(<ActivityPanel />)
    expect(await screen.findByText('17%')).toBeDefined() // asked you, of what it took in
    expect(activity).toHaveBeenCalledWith(30)
    // Each strip is titled: identity never rests on colour.
    for (const title of ['Taken in', 'Asked you', 'Retired on its own', 'Decided by you']) {
      expect(screen.getByText(title)).toBeDefined()
    }
    expect(screen.getByText('gate 0.80')).toBeDefined()
  })

  it('reads out the whole day under the pointer', async () => {
    render(<ActivityPanel />)
    const [strip] = await screen.findAllByRole('img')
    strip.getBoundingClientRect = () => ({ left: 0, width: 300 }) as DOMRect
    fireEvent.pointerMove(strip, { clientX: 250 })
    expect(
      // The day in the viewer's locale ("27 Oct" or "Oct 27"), then that day's counts.
      await screen.findByText(/27.* · 2 taken in · 0 asked · 1 retired on its own · 2 decided · 1 via an agent/),
    ).toBeDefined()
  })

  it('has a table view, and a range above the charts', async () => {
    render(<ActivityPanel />)
    await screen.findByText('17%')
    await userEvent.click(screen.getByRole('button', { name: 'Show as a table' }))
    // Days with nothing in them are left out; newest first.
    const rows = screen.getAllByRole('row').slice(1)
    const dates = rows.map((r) => (r as HTMLTableRowElement).cells[0].textContent ?? '')
    expect(dates.map((d) => /\d+/.exec(d)?.[0])).toEqual(['27', '25'])

    await userEvent.click(screen.getByRole('button', { name: '90d' }))
    await waitFor(() => expect(activity).toHaveBeenLastCalledWith(90))
  })
})
