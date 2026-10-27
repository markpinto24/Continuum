import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPanel } from '@/components/chat-panel'
import { TooltipProvider } from '@/components/ui/tooltip'
import type { ChatContext, ChatDone, IngestResponse, Question } from '@/lib/types'
import { makeMemory } from '@/test/fixtures'

/**
 * The answer does not wait for memory (TODO Iteration 5): `done` frees the
 * input, and the turn's write-back arrives later in its own event.
 */

const api = vi.hoisted(() => ({ streamChat: vi.fn() }))

const ME = { user_id: 'mark', email: 'mark@example.com', is_admin: true, via: 'session' as const, mode: 'workspace' as const, default_mode: 'workspace' as const }

vi.mock('@/lib/api', () => ({
  api: {
    speechStatus: () => Promise.resolve({ enabled: false, ready: false, max_seconds: 120, language: 'en' }),
  },
  streamChat: api.streamChat,
}))

const CONTEXT: ChatContext = { query: 'q', memories: [], disagreements: [], pending: [], as_of: null }

function done(questions: Question[] = []): ChatDone {
  return { memory_ids: [], cited_ids: [], disagreements: 0, remembered: null, remembering: true, questions }
}

function written(change: Partial<IngestResponse> = {}): IngestResponse {
  return {
    source_id: 's', extracted: 0, created: [], duplicates_skipped: 0, reinforced: [], superseded: [],
    conflicts_raised: [], team_conflicts: [], pending: [], confirmed: [], resolutions: [], ...change,
  }
}

/** A stream whose `remembered` event waits until `land()` is called. */
function heldStream(answer: string, remembered: IngestResponse, questions: Question[] = []) {
  let land!: () => void
  const landed = new Promise<void>((resolve) => (land = resolve))
  api.streamChat.mockImplementationOnce(async (_body, handlers) => {
    handlers.onContext?.(CONTEXT)
    handlers.onDelta?.(answer)
    handlers.onDone?.(done(questions))
    await landed
    handlers.onRemembered?.({ remembered })
  })
  return () => land()
}

function renderPanel(onGraphChanged = vi.fn()) {
  render(
    <TooltipProvider>
      <ChatPanel me={ME} openDisputes={0} onGraphChanged={onGraphChanged} onSelectMemory={() => {}} />
    </TooltipProvider>,
  )
  return onGraphChanged
}

beforeEach(() => {
  api.streamChat.mockReset()
})

describe('memory lands after the answer', () => {
  it('frees the input at done and says it is remembering until the write lands', async () => {
    const onGraphChanged = renderPanel()
    const land = heldStream('Noted.', written({ extracted: 1, created: [makeMemory({ id: 'n1', content: 'Atlas uses Postgres' })] }))

    await userEvent.type(screen.getByRole('textbox'), 'we use Postgres{Enter}')
    await screen.findByText('Remembering…')
    // (That the next question can be sent now is the next test.)
    expect(onGraphChanged).not.toHaveBeenCalled()

    land()
    expect(await screen.findByText(/Stored 1 new memory/)).toBeDefined()
    expect(screen.queryByText('Remembering…')).toBeNull()
    expect(onGraphChanged).toHaveBeenCalled()
  })

  it('puts a late write-back on its own turn, not on a newer one', async () => {
    renderPanel()
    const landFirst = heldStream('First answer.', written({ extracted: 1, created: [makeMemory({ id: 'n1' })] }))
    await userEvent.type(screen.getByRole('textbox'), 'first{Enter}')
    await screen.findByText('First answer.')

    const landSecond = heldStream('Second answer.', written({ extracted: 0 }))
    await userEvent.type(screen.getByRole('textbox'), 'second{Enter}')
    await screen.findByText('Second answer.')
    landSecond()
    await screen.findByText(/Nothing stored/)

    landFirst()
    await screen.findByText(/Stored 1 new memory/)
    // Each turn reads its own outcome: the second still says nothing was stored.
    expect(screen.getAllByText(/Nothing stored/)).toHaveLength(1)
    expect(screen.queryByText('Remembering…')).toBeNull()
  })

  it('drops the question about a maybe the turn itself just settled', async () => {
    renderPanel()
    const question: Question = {
      kind: 'pending', memory_id: 'p1', text: 'Still thinking about a Sunday long run?',
      options: [{ label: 'Yes', action: 'confirm' }, { label: 'Still unsure', action: 'unsure' }, { label: 'No', action: 'decline' }],
    }
    const land = heldStream('Good plan.', written({ extracted: 1, confirmed: ['p1'] }), [question])
    await userEvent.type(screen.getByRole('textbox'), 'I will add a Sunday long run{Enter}')
    await screen.findByText(question.text)

    land()
    await waitFor(() => expect(screen.queryByText(question.text)).toBeNull())
  })
})
