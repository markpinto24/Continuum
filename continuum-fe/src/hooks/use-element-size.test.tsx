import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { useElementSize } from './use-element-size'

/** Records what is being observed, so the test can see which element is watched. */
class RecordingResizeObserver {
  static instances: RecordingResizeObserver[] = []
  observed: Element[] = []
  disconnected = false
  constructor(private readonly callback: ResizeObserverCallback) {
    RecordingResizeObserver.instances.push(this)
  }
  observe(el: Element) {
    this.observed.push(el)
  }
  unobserve() {}
  disconnect() {
    this.disconnected = true
  }
  fire(width: number, height: number) {
    this.callback(
      [{ contentRect: { width, height } } as ResizeObserverEntry],
      this as unknown as ResizeObserver,
    )
  }
}

const original = globalThis.ResizeObserver
beforeEach(() => {
  RecordingResizeObserver.instances = []
  globalThis.ResizeObserver = RecordingResizeObserver as unknown as typeof ResizeObserver
})
afterEach(() => {
  globalThis.ResizeObserver = original
})

function Probe({ swap }: { swap: boolean }) {
  const [ref, size] = useElementSize<HTMLDivElement>()
  // Two different elements across renders — the empty-state/canvas swap.
  return swap ? (
    <section ref={ref} data-testid="second">{`${size.width}x${size.height}`}</section>
  ) : (
    <div ref={ref} data-testid="first">{`${size.width}x${size.height}`}</div>
  )
}

describe('useElementSize', () => {
  it('re-attaches when the measured element is replaced', () => {
    // The bug this guards: a mount-only effect kept observing the first
    // element after it left the document, so later resizes never arrived.
    const { rerender, getByTestId } = render(<Probe swap={false} />)
    rerender(<Probe swap />)

    const live = RecordingResizeObserver.instances.filter((o) => !o.disconnected)
    expect(live).toHaveLength(1)
    expect(live[0].observed).toEqual([getByTestId('second')])
    expect(live[0].observed[0].isConnected).toBe(true)
  })

  it('disconnects the observer of the element that went away', () => {
    const { rerender } = render(<Probe swap={false} />)
    const first = RecordingResizeObserver.instances[0]
    rerender(<Probe swap />)

    expect(first.disconnected).toBe(true)
  })

  it('reports the size the observer sees', () => {
    const { getByTestId } = render(<Probe swap={false} />)
    act(() => RecordingResizeObserver.instances[0].fire(640.4, 480.6))

    expect(getByTestId('first').textContent).toBe('640x481')
  })
})
