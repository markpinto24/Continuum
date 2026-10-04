import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPanel } from '@/components/chat-panel'
import { MemoryDetail } from '@/components/memory-detail'
import { TooltipProvider } from '@/components/ui/tooltip'
import type { Memory } from '@/lib/types'
import { makeMemory } from '@/test/fixtures'

const ME = { user_id: 'mark', email: 'mark@example.com', is_admin: true, via: 'session' as const }

const api = vi.hoisted(() => ({ memory: vi.fn(), shareMemory: vi.fn(), streamChat: vi.fn() }))

vi.mock('@/lib/api', () => ({
  api: {
    memory: api.memory,
    shareMemory: api.shareMemory,
    reinforce: vi.fn(),
    reactivate: vi.fn(),
    speechStatus: () => Promise.resolve({ enabled: false, ready: false, max_seconds: 120, language: 'en', synthesis: false, synthesis_ready: false, synthesis_max_chars: 800 }),
  },
  streamChat: api.streamChat,
}))

function memory(over: Partial<Memory> = {}): Memory {
  return makeMemory({
    content: 'Raj leads the mobile team', category: 'person', subject: 'mobile-team',
    created_at: '2026-10-01T00:00:00Z', ...over,
  })
}

beforeEach(() => {
  api.memory.mockReset()
  api.shareMemory.mockReset()
  api.streamChat.mockReset().mockResolvedValue(undefined)
})

describe('sharing a memory', () => {
  it('shares, says what the team space made of it, and opens the shared copy', async () => {
    api.memory.mockResolvedValue(memory())
    api.shareMemory.mockResolvedValue({ outcome: 'merged', original: memory(), shared: memory({ id: 's1', user_id: '_shared' }) })
    const onSelect = vi.fn()
    render(<MemoryDetail memoryId="m1" onSelect={onSelect} onMutated={vi.fn()} />)

    await userEvent.click(await screen.findByRole('button', { name: /share with team/i }))

    expect(api.shareMemory).toHaveBeenCalledWith('m1')
    expect(await screen.findByText(/team already knew this/)).toBeDefined()
    expect(onSelect).toHaveBeenCalledWith('s1')
  })

  it('marks team knowledge with who shared it, and offers no share button', async () => {
    api.memory.mockResolvedValue(memory({ user_id: '_shared', shared_by_email: 'sara@example.com' }))
    render(<MemoryDetail memoryId="m1" onSelect={vi.fn()} onMutated={vi.fn()} />)

    expect(await screen.findByText('Shared by sara@example.com')).toBeDefined()
    expect(screen.queryByRole('button', { name: /share with team/i })).toBeNull()
  })

  it('a disputed memory cannot be shared until it is settled', async () => {
    api.memory.mockResolvedValue(memory({ status: 'contradicted', conflicts_with: ['m2'] }))
    render(<MemoryDetail memoryId="m1" onSelect={vi.fn()} onMutated={vi.fn()} />)
    await screen.findByText('Raj leads the mobile team')
    expect(screen.queryByRole('button', { name: /share with team/i })).toBeNull()
  })
})

describe('sharing what you say in chat', () => {
  async function chat() {
    render(
      <TooltipProvider>
        <ChatPanel me={ME} openDisputes={0} onGraphChanged={() => {}} onSelectMemory={() => {}} />
      </TooltipProvider>,
    )
  }

  it('is private by default', async () => {
    await chat()
    await userEvent.type(screen.getByRole('textbox'), 'Raj leads mobile{Enter}')
    await waitFor(() => expect(api.streamChat).toHaveBeenCalled())
    expect(api.streamChat.mock.calls[0][0].share).toBe(false)
  })

  it('shares turns once the toggle is on, and says so', async () => {
    await chat()
    await act(async () => {
      screen.getByRole('button', { name: 'Share what I say with the team' }).click()
    })
    expect(screen.getByText(/Sharing on: what you say is remembered as team knowledge/)).toBeDefined()
    await userEvent.type(screen.getByRole('textbox'), 'Raj leads mobile{Enter}')
    await waitFor(() => expect(api.streamChat).toHaveBeenCalled())
    expect(api.streamChat.mock.calls[0][0].share).toBe(true)
  })
})
