import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { PendingQuestionCard, RememberedList } from '@/components/turn-outcome'
import { questionFor } from '@/lib/pending'
import type { IngestResponse } from '@/lib/types'
import { makeMemory } from '@/test/fixtures'

const api = vi.hoisted(() => ({
  rejectMemory: vi.fn(),
  confirmPending: vi.fn(),
  declinePending: vi.fn(),
  unsurePending: vi.fn(),
}))
vi.mock('@/lib/api', () => ({ api }))

function remembered(over: Partial<IngestResponse> = {}): IngestResponse {
  return {
    source_id: 's', extracted: 1, created: [], duplicates_skipped: 0, reinforced: [],
    superseded: [], conflicts_raised: [], team_conflicts: [], pending: [], confirmed: [],
    resolutions: [], ...over,
  }
}

beforeEach(() => Object.values(api).forEach((fn) => fn.mockReset()))

describe('what a turn remembered', () => {
  it('says the stored choice back, and Undo is "this isn’t a real fact"', async () => {
    const split = makeMemory({ id: 'ppl', content: 'The user follows a push/pull/legs split' })
    const onChanged = vi.fn()
    render(<RememberedList remembered={remembered({ created: [split] })} onChanged={onChanged} onSelect={vi.fn()} />)

    expect(screen.getByText(/Remembered:/)).toBeDefined()
    await userEvent.click(screen.getByRole('button', { name: /Undo/ }))

    expect(api.rejectMemory).toHaveBeenCalledWith('ppl', 'misread', 'Undone from the chat')
    expect(onChanged).toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: /Undo/ })).toBeNull()
  })

  it('says a maybe was kept as undecided, not remembered as true', () => {
    const maybe = makeMemory({ id: 'run', status: 'pending', content: 'Considering a Sunday run' })
    render(<RememberedList remembered={remembered({ created: [maybe], pending: ['run'] })} onChanged={vi.fn()} onSelect={vi.fn()} />)
    expect(screen.getByText(/Saved as undecided:/)).toBeDefined()
    expect(screen.getByText(/I'll ask when it comes up/)).toBeDefined()
  })
})

describe('a question with options', () => {
  const maybe = makeMemory({ id: 'run', status: 'pending', content: 'Considering a Sunday run' })

  it('offers fixed answers and says what each did', async () => {
    api.confirmPending.mockResolvedValueOnce(remembered({ superseded: ['old'] }))
    const onAnswered = vi.fn()
    render(<PendingQuestionCard question={questionFor(maybe)} onAnswered={onAnswered} />)

    expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual([
      "Yes, I'm going with it", 'Still unsure', 'No',
    ])
    await userEvent.click(screen.getByRole('button', { name: "Yes, I'm going with it" }))

    expect(api.confirmPending).toHaveBeenCalledWith('run')
    expect(onAnswered).toHaveBeenCalledWith('Settled — and it replaces what you did before.')
  })

  it('shows the answer instead of the buttons once answered', () => {
    render(<PendingQuestionCard question={questionFor(maybe)} outcome="Still undecided." onAnswered={vi.fn()} />)
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByText('Still undecided.')).toBeDefined()
  })

  it('keeps the buttons and says why when the answer fails', async () => {
    api.unsurePending.mockRejectedValueOnce(new Error('This memory is declined, not pending.'))
    render(<PendingQuestionCard question={questionFor(maybe)} onAnswered={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: 'Still unsure' }))
    expect(await screen.findByText(/declined, not pending/)).toBeDefined()
    expect(screen.getByRole('button', { name: 'No' })).toBeDefined()
  })
})
