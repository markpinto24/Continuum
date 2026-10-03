import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AnswerFeedback } from '@/components/answer-feedback'
import { ChatPanel } from '@/components/chat-panel'
import { MemoryDetail } from '@/components/memory-detail'
import { TooltipProvider } from '@/components/ui/tooltip'
import type { ChatContext } from '@/lib/types'
import { makeMemory } from '@/test/fixtures'

const api = vi.hoisted(() => ({
  memory: vi.fn(),
  rejectMemory: vi.fn(),
  forgetMemory: vi.fn(),
  rateAnswer: vi.fn(),
  searchMemories: vi.fn(),
  streamChat: vi.fn(),
}))

vi.mock('@/lib/api', () => ({
  api: {
    memory: api.memory,
    rejectMemory: api.rejectMemory,
    forgetMemory: api.forgetMemory,
    rateAnswer: api.rateAnswer,
    searchMemories: api.searchMemories,
    reinforce: vi.fn(),
    reactivate: vi.fn(),
    shareMemory: vi.fn(),
    speechStatus: () =>
      Promise.resolve({ enabled: false, ready: false, max_seconds: 120, language: 'en', synthesis: false, synthesis_ready: false, synthesis_max_chars: 800 }),
  },
  streamChat: api.streamChat,
}))

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset()
  api.streamChat.mockResolvedValue(undefined)
})

function detail(userId = 'mark', isAdmin = false) {
  render(
    <MemoryDetail memoryId="m1" onSelect={vi.fn()} onMutated={vi.fn()} userId={userId} isAdmin={isAdmin} />,
  )
}

describe('correcting a memory', () => {
  it('rejects a misreading with a reason and says what came back', async () => {
    api.memory.mockResolvedValue(makeMemory({ content: 'Atlas uses Mongo?' }))
    api.rejectMemory.mockResolvedValue({ memory: makeMemory(), restored: ['old'] })
    detail()

    await userEvent.click(await screen.findByRole('button', { name: /isn’t a real fact/i }))
    await userEvent.click(screen.getByRole('button', { name: /not a fact/i }))

    expect(api.rejectMemory).toHaveBeenCalledWith('m1', 'not_a_fact')
    expect(await screen.findByText(/1 belief it had replaced is active again/)).toBeDefined()
  })

  it('forgets only after a second, explicit confirmation', async () => {
    api.memory.mockResolvedValue(makeMemory())
    api.forgetMemory.mockResolvedValue(makeMemory())
    detail()

    await userEvent.click(await screen.findByRole('button', { name: /forget…/i }))
    expect(api.forgetMemory).not.toHaveBeenCalled()
    expect(screen.getByText(/cannot be undone/)).toBeDefined()
    await userEvent.click(screen.getByRole('button', { name: /forget permanently/i }))
    expect(api.forgetMemory).toHaveBeenCalledWith('m1')
  })

  it('is not offered on team knowledge someone else shared, unless you are an admin', async () => {
    const theirs = makeMemory({ user_id: '_shared', shared_by: 'sara' })
    api.memory.mockResolvedValue(theirs)
    detail('mark', false)
    await screen.findByText('Atlas runs on Postgres')
    expect(screen.queryByRole('button', { name: /forget…/i })).toBeNull()
  })

  it('a forgotten memory says so and offers nothing to undo', async () => {
    api.memory.mockResolvedValue(
      makeMemory({ content: '[forgotten]', redacted_at: '2026-10-03T00:00:00Z', status: 'archived' }),
    )
    detail()
    expect(await screen.findByText(/Its words are gone/)).toBeDefined()
    expect(screen.queryByRole('button', { name: /restore/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /still true/i })).toBeNull()
  })

  it('a summary lists what it was written from and cannot be confirmed', async () => {
    api.memory.mockResolvedValue(makeMemory({ kind: 'summary', derived_from: ['aaaaaaaa1', 'bbbbbbbb2'] }))
    detail()
    expect(await screen.findByText(/Written from 2 memories/)).toBeDefined()
    expect(screen.getByText('aaaaaaaa…')).toBeDefined()
    expect(screen.queryByRole('button', { name: /still true/i })).toBeNull()
  })
})

describe('rating an answer', () => {
  const context: ChatContext = {
    query: 'q',
    memories: [{ memory: makeMemory({ id: 'used' }), similarity: 0.7, recency: 1, keyword: 0, score: 0.5 }],
    disagreements: [],
    as_of: null,
  }

  it('a thumbs up is sent at once', async () => {
    api.rateAnswer.mockResolvedValue({ missing: 0 })
    render(<AnswerFeedback question="what db?" answer="Postgres [1]" context={context} />)
    await userEvent.click(screen.getByRole('button', { name: 'Helpful answer' }))
    expect(api.rateAnswer).toHaveBeenCalledWith(
      expect.objectContaining({ query: 'what db?', rating: 1, used_ids: ['used'], missing_ids: [] }),
    )
  })

  it('names the memory that should have come up', async () => {
    api.searchMemories.mockResolvedValue({
      query: 'billing',
      results: [
        { memory: makeMemory({ id: 'used', content: 'Already used' }), score: 0.9 },
        { memory: makeMemory({ id: 'missed', content: 'Billing runs on ECS' }), score: 0.8 },
      ],
    })
    api.rateAnswer.mockResolvedValue({ missing: 1 })
    render(<AnswerFeedback question="where does billing run?" answer="No idea." context={context} />)

    await userEvent.click(screen.getByRole('button', { name: 'Unhelpful answer' }))
    await userEvent.type(screen.getByRole('textbox', { name: /missing memory/i }), 'billing{Enter}')
    expect(screen.queryByText('Already used')).toBeNull() // it was in the answer already
    await userEvent.click(await screen.findByRole('button', { name: /Billing runs on ECS/ }))
    await userEvent.click(screen.getByRole('button', { name: /1 should have come up/ }))

    expect(api.rateAnswer).toHaveBeenCalledWith(
      expect.objectContaining({ rating: -1, missing_ids: ['missed'] }),
    )
    expect(await screen.findByText(/kept as a retrieval test case/)).toBeDefined()
  })
})

describe('asking about the past', () => {
  it('sends the date and says nothing is remembered', async () => {
    render(
      <TooltipProvider>
        <ChatPanel
          me={{ user_id: 'mark', email: 'm@x.io', is_admin: false, via: 'session' }}
          openDisputes={0}
          asOf="2026-03-15T23:59:59.000Z"
          onGraphChanged={() => {}}
          onSelectMemory={() => {}}
        />
      </TooltipProvider>,
    )
    expect(screen.getByText(/nothing you say now is remembered/)).toBeDefined()
    await userEvent.type(screen.getByRole('textbox'), 'what db did we use?{Enter}')
    await waitFor(() => expect(api.streamChat).toHaveBeenCalled())
    expect(api.streamChat.mock.calls[0][0].as_of).toBe('2026-03-15T23:59:59.000Z')
  })
})
