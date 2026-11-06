import { act, render, renderHook, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { BlurWords } from '@/components/blur-words'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { usePresence } from '@/lib/motion'

describe('usePresence', () => {
  it('stays present after closing until the exit animation says it is done', () => {
    const { result, rerender } = renderHook(({ open }) => usePresence(open), {
      initialProps: { open: true },
    })
    expect(result.current.present).toBe(true)

    rerender({ open: false })
    expect(result.current.present).toBe(true) // still on screen, leaving

    act(() => result.current.exited())
    expect(result.current.present).toBe(false)
  })

  it('opening again mid-exit keeps it present, and a late exit cannot remove it', () => {
    const { result, rerender } = renderHook(({ open }) => usePresence(open), {
      initialProps: { open: true },
    })
    rerender({ open: false })
    const exitedFromTheOldAnimation = result.current.exited
    rerender({ open: true })
    act(() => exitedFromTheOldAnimation())
    expect(result.current.present).toBe(true)
  })

  it('is absent when it was never opened', () => {
    const { result } = renderHook(() => usePresence(false))
    expect(result.current.present).toBe(false)
  })
})

describe('Dialog', () => {
  function Example({ open }: { open: boolean }) {
    return (
      <Dialog open={open} onOpenChange={() => {}}>
        <DialogContent aria-describedby={undefined}>
          <DialogTitle>Example</DialogTitle>
        </DialogContent>
      </Dialog>
    )
  }

  it('animates out before it leaves, then leaves for good', async () => {
    const { rerender } = render(<Example open />)
    expect(screen.getByRole('dialog', { name: 'Example' })).toBeTruthy()
    rerender(<Example open={false} />)
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Example' })).toBeNull())
  })

  it('renders nothing while closed', () => {
    render(<Example open={false} />)
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

describe('BlurWords', () => {
  it('reads the line once, whole, and hides the animated words from screen readers', () => {
    const { container } = render(<BlurWords text="Good morning, Mark." />)
    expect(screen.getByText('Good morning, Mark.')).toBeTruthy()
    const words = container.querySelectorAll('[data-word]')
    expect(words).toHaveLength(3)
    expect([...words].every((word) => word.getAttribute('aria-hidden') === 'true')).toBe(true)
  })
})
