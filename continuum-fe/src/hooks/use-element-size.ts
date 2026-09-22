import { useCallback, useLayoutEffect, useState } from 'react'

/**
 * Track an element's box. The WebGL canvas needs explicit pixel dimensions —
 * it cannot be sized by CSS alone — so the layout has to be measured and fed in.
 *
 * Returns a *callback* ref, not a ref object. A ref object plus a mount-only
 * effect observes whichever element existed on the first render and never
 * re-attaches: if the component later renders a different element (an empty
 * state swapping for the canvas container, say), the observer keeps watching a
 * node that is no longer in the document, and every later resize — including
 * dragging the sidebar — is silently ignored.
 */
export function useElementSize<T extends HTMLElement>() {
  const [element, setElement] = useState<T | null>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })

  useLayoutEffect(() => {
    if (!element) return

    // Measure synchronously so the first paint has real dimensions, rather than
    // rendering one frame at 0x0 and waiting for the observer's first callback.
    const box = element.getBoundingClientRect()
    setSize({ width: Math.round(box.width), height: Math.round(box.height) })

    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      setSize((prev) =>
        prev.width === Math.round(width) && prev.height === Math.round(height)
          ? prev
          : { width: Math.round(width), height: Math.round(height) },
      )
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [element])

  const ref = useCallback((node: T | null) => setElement(node), [])

  return [ref, size] as const
}
