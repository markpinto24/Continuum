import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { AppHeader } from '@/components/app-header'
import { TooltipProvider } from '@/components/ui/tooltip'

function header(asOf: string | null, onAsOf = vi.fn()) {
  render(
    <TooltipProvider>
      <AppHeader
        me={{ user_id: 'mark', email: 'm@x.io', is_admin: false, via: 'session' }}
        onAccount={vi.fn()}
        onSignOut={vi.fn()}
        health={null}
        nodeCount={0}
        edgeCount={0}
        includeArchived={false}
        onIncludeArchived={vi.fn()}
        onRefresh={vi.fn()}
        refreshing={false}
        asOf={asOf}
        onAsOf={onAsOf}
      />
    </TooltipProvider>,
  )
  return onAsOf
}

const pad = (n: number) => String(n).padStart(2, '0')
const now = new Date()
const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`

describe('the as-of picker', () => {
  it('shows today when looking at the present', () => {
    header(null)
    expect((screen.getByLabelText('As of') as HTMLInputElement).value).toBe(today)
  })

  it('a past day looks back; picking today again means now', () => {
    const onAsOf = header('2026-03-15')
    const input = screen.getByLabelText('As of')
    fireEvent.change(input, { target: { value: '2026-03-01' } })
    expect(onAsOf).toHaveBeenLastCalledWith('2026-03-01')
    fireEvent.change(input, { target: { value: today } })
    expect(onAsOf).toHaveBeenLastCalledWith(null)
  })
})
