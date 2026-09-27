import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPanel } from '@/components/chat-panel'
import { TooltipProvider } from '@/components/ui/tooltip'
import type { ChatContext, Memory } from '@/lib/types'

const api = vi.hoisted(() => ({ resolveConflict: vi.fn(), streamChat: vi.fn() }))

vi.mock('@/lib/api', () => ({
  api: {
    speechStatus: () => Promise.resolve({ enabled: false, ready: false, max_seconds: 120, language: 'en' }),
    resolveConflict: api.resolveConflict,
  },
  streamChat: api.streamChat,
}))

function memory(id: string, content: string, created: string): Memory {
  return {
    id, user_id: 'mark', content, category: 'decision', subject: 'atlas', confidence: 0.7,
    status: 'contradicted', source_id: null, source_excerpt: null, supersedes: [],
    superseded_by: null, conflicts_with: [], escalations: [], created_at: created,
    updated_at: created, last_reinforced_at: created, reinforcement_count: 0,
  }
}

const postgres = memory('m1', 'Atlas runs on Postgres', '2026-03-01T00:00:00Z')
const mongo = memory('m2', 'Atlas runs on Mongo', '2026-04-15T00:00:00Z')

const disputed: ChatContext = {
  query: 'db?',
  memories: [],
  disagreements: [{ subject: 'atlas', memories: [postgres, mongo] }],
}

beforeEach(() => {
  api.resolveConflict.mockReset().mockResolvedValue({})
  api.streamChat.mockReset().mockImplementation(async (_body, handlers) => {
    handlers.onContext?.(disputed)
    handlers.onDelta?.('The record disagrees: Postgres [1] or Mongo [2]. Which holds?')
    handlers.onDone?.({ memory_ids: [], cited_ids: [], disagreements: 1, remembered: null })
  })
})

async function askAndRender(onGraphChanged = vi.fn()) {
  render(
    <TooltipProvider>
      <ChatPanel onGraphChanged={onGraphChanged} onSelectMemory={() => {}} />
    </TooltipProvider>,
  )
  await userEvent.type(screen.getByRole('textbox'), 'what database?{Enter}')
  await screen.findAllByRole('button', { name: 'This holds' })
  return onGraphChanged
}

describe('settling a dispute in the chat', () => {
  it('names the side the person picked as the winner', async () => {
    const onGraphChanged = await askAndRender()
    const holds = screen.getAllByRole('button', { name: 'This holds' })
    await userEvent.click(holds[1]) // Mongo

    expect(api.resolveConflict).toHaveBeenCalledWith({
      winner_id: 'm2', loser_ids: ['m1'], keep_both: false,
    })
    expect(await screen.findByText(/Settled: “Atlas runs on Mongo” holds\./)).toBeDefined()
    expect(onGraphChanged).toHaveBeenCalled() // graph and inbox refresh
    expect(screen.queryByRole('button', { name: 'This holds' })).toBeNull() // not offered twice
  })

  it('offers both-are-true as a first-class answer', async () => {
    await askAndRender()
    await userEvent.click(screen.getByRole('button', { name: 'Both are true' }))

    expect(api.resolveConflict).toHaveBeenCalledWith({
      winner_id: 'm1', loser_ids: ['m2'], keep_both: true,
    })
    expect(await screen.findByText(/Kept both/)).toBeDefined()
  })

  it('keeps the choice open and says why when the decision fails', async () => {
    api.resolveConflict.mockRejectedValue(new Error('Winner memory not found'))
    await askAndRender()
    await userEvent.click(screen.getAllByRole('button', { name: 'This holds' })[0])

    await waitFor(() => expect(screen.getByText('Winner memory not found')).toBeDefined())
    expect(screen.getAllByRole('button', { name: 'This holds' })).toHaveLength(2)
  })
})
