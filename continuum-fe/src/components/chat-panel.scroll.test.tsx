import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPanel } from '@/components/chat-panel'
import { TooltipProvider } from '@/components/ui/tooltip'
import type { ChatHandlers } from '@/lib/api'

/**
 * Reading back while an answer streams. jsdom does no layout, so the scroller
 * is given its sizes by hand: 2000px of conversation in a 500px window.
 */

const api = vi.hoisted(() => ({ streamChat: vi.fn() }))
const ME = { user_id: 'mark', email: 'mark@example.com', is_admin: false, via: 'session' as const, mode: 'personal' as const, default_mode: 'personal' as const }

vi.mock('@/lib/api', () => ({
  api: { speechStatus: () => Promise.resolve({ enabled: false, ready: false, max_seconds: 120, language: 'en' }) },
  streamChat: api.streamChat,
}))

let handlers: ChatHandlers | null = null
let finish: () => void = () => {}

beforeEach(() => {
  handlers = null
  api.streamChat.mockReset().mockImplementation(
    (_body: unknown, h: ChatHandlers) =>
      new Promise<void>((resolve) => {
        handlers = h
        finish = () => {
          h.onDone?.({ memory_ids: [], cited_ids: [], disagreements: 0, remembered: null, questions: [] })
          resolve()
        }
      }),
  )
})

function scroller(): HTMLElement {
  const view = screen.getByRole('textbox').closest('div.flex')?.parentElement?.querySelector('.overflow-y-auto')
  if (!(view instanceof HTMLElement)) throw new Error('no scroller')
  Object.defineProperty(view, 'scrollHeight', { configurable: true, value: 2000 })
  Object.defineProperty(view, 'clientHeight', { configurable: true, value: 500 })
  view.scrollTo = vi.fn()
  return view
}

async function startAnswer() {
  render(
    <TooltipProvider>
      <ChatPanel me={ME} openDisputes={0} onGraphChanged={() => {}} onSelectMemory={() => {}} />
    </TooltipProvider>,
  )
  const view = scroller()
  await userEvent.type(screen.getByRole('textbox'), 'recommend a workout split{Enter}')
  await act(async () => handlers?.onDelta?.('Monday: chest. '))
  return view
}

describe('reading back while an answer streams', () => {
  it('follows the answer while you are at the bottom', async () => {
    const view = await startAnswer()
    await act(async () => handlers?.onDelta?.('Tuesday: back. '))
    expect(view.scrollTop).toBe(2000)
  })

  it('stays where you scrolled to, and offers a way back down', async () => {
    const view = await startAnswer()
    view.scrollTop = 300 // reading an earlier answer
    fireEvent.scroll(view)

    await act(async () => handlers?.onDelta?.('Tuesday: back. Wednesday: rest. '))
    expect(view.scrollTop).toBe(300) // not yanked back to the bottom

    await userEvent.click(screen.getByRole('button', { name: /Jump to the answer/ }))
    expect(view.scrollTo).toHaveBeenCalledWith({ top: 2000, behavior: 'smooth' })
    await act(async () => finish())
  })

  it('a new question follows its answer again', async () => {
    const view = await startAnswer()
    view.scrollTop = 300
    fireEvent.scroll(view)
    await act(async () => finish())

    await userEvent.type(screen.getByRole('textbox'), 'and for 5 days?{Enter}')
    await act(async () => handlers?.onDelta?.('Drop Saturday. '))
    expect(view.scrollTop).toBe(2000)
  })
})
