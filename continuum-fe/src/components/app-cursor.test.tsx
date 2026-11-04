import { act, fireEvent, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AppCursor } from '@/components/app-cursor'

function pointer(fine: boolean) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query === '(pointer: fine)' ? fine : false,
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
    pointer(true)
    const { unmount } = render(<AppCursor />)
    expect(document.documentElement.classList.contains('has-app-cursor')).toBe(true)
    unmount()
    expect(document.documentElement.classList.contains('has-app-cursor')).toBe(false)
  })

  it('is not there at all on a touch screen', () => {
    pointer(false)
    const { container } = render(<AppCursor />)
    expect(container.firstChild).toBeNull()
    expect(document.documentElement.classList.contains('has-app-cursor')).toBe(false)
  })

  it('shows itself, steps aside over a text field, and grows over a button', () => {
    pointer(true)
    const { container } = render(
      <>
        <AppCursor />
        <input aria-label="field" />
        <button type="button">Go</button>
      </>,
    )
    const faces = () => [...container.querySelectorAll<HTMLElement>('.rounded-full')]
    act(() => {
      fireEvent.pointerMove(window, { clientX: 10, clientY: 10 })
    })
    expect(faces()[1].style.opacity).toBe('1') // the dot is where the pointer is

    act(() => {
      fireEvent.pointerOver(container.querySelector('input')!)
    })
    expect(faces().map((f) => f.style.opacity)).toEqual(['0', '0']) // the native caret instead

    act(() => {
      fireEvent.pointerOver(container.querySelector('button')!)
    })
    expect(faces()[0].style.opacity).toBe('1')
    expect(faces()[0].style.transform).toBe('scale(1.55)')
  })
})
