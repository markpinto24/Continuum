import { act, fireEvent, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AppCursor } from '@/components/app-cursor'

function media({ fine, reduced = false }: { fine: boolean; reduced?: boolean }) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches:
      query === '(pointer: fine)' ? fine : query === '(prefers-reduced-motion: reduce)' ? reduced : false,
    addEventListener: () => {},
    removeEventListener: () => {},
  }))
}

afterEach(() => {
  vi.unstubAllGlobals()
  document.documentElement.classList.remove('has-app-cursor')
})

describe('the app cursor', () => {
  it('takes over from the native cursor with a mouse, and gives it back when it goes', () => {
    media({ fine: true })
    const { unmount } = render(<AppCursor />)
    expect(document.documentElement.classList.contains('has-app-cursor')).toBe(true)
    unmount()
    expect(document.documentElement.classList.contains('has-app-cursor')).toBe(false)
  })

  it('is not there at all on a touch screen', () => {
    media({ fine: false })
    const { container } = render(<AppCursor />)
    expect(container.firstChild).toBeNull()
    expect(document.documentElement.classList.contains('has-app-cursor')).toBe(false)
  })

  it('shows itself, steps aside over a text field, and wraps a button', () => {
    media({ fine: true })
    const { container } = render(
      <>
        <AppCursor />
        <input aria-label="field" />
        <button type="button">Go</button>
      </>,
    )
    const cursor = container.querySelector<HTMLElement>('.app-cursor')!
    // jsdom lays nothing out; give the button a button's size.
    const button = container.querySelector('button')!
    button.getBoundingClientRect = () => new DOMRect(20, 20, 90, 32)

    act(() => {
      fireEvent.pointerMove(window, { clientX: 10, clientY: 10 })
    })
    expect(cursor.dataset.shown).toBe('true')

    act(() => {
      fireEvent.pointerOver(container.querySelector('input')!)
    })
    expect(cursor.dataset.over).toBe('text') // CSS hides it: the native caret instead

    act(() => {
      fireEvent.pointerOver(button)
    })
    expect(cursor.dataset.over).toBe('action')
    expect(cursor.dataset.wrap).toBe('true')
  })

  it('ripples on a press, but not when motion is reduced', () => {
    media({ fine: true })
    const { container, unmount } = render(<AppCursor />)
    act(() => {
      fireEvent.pointerDown(window, { clientX: 5, clientY: 5 })
    })
    expect(container.querySelector('.app-cursor-ripple')).not.toBeNull()
    expect(container.querySelector<HTMLElement>('.app-cursor')!.dataset.pressed).toBe('true')
    unmount()

    media({ fine: true, reduced: true })
    const still = render(<AppCursor />)
    act(() => {
      fireEvent.pointerDown(window, { clientX: 5, clientY: 5 })
    })
    expect(still.container.querySelector('.app-cursor-ripple')).toBeNull()
    expect(still.container.querySelectorAll('.app-cursor-tail')).toHaveLength(0)
  })
})
