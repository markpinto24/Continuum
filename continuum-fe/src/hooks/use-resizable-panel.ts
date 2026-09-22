import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'

import { PANEL_DEFAULT, clampPanelWidth } from '@/lib/panel-width'

const STORAGE_KEY = 'continuum.panel_width'
const KEY_STEP = 24

/**
 * Width state for a right-hand sidebar resized by dragging its left edge.
 *
 * Pointer capture is what makes the drag reliable here: without it, moving the
 * cursor across the WebGL canvas hands the pointer events to three.js's orbit
 * controls, and the drag stops dead halfway across the graph.
 *
 * The width is remembered per viewer in localStorage — a convenience, so every
 * access is guarded; a private window or blocked storage just means the default.
 */
export function useResizablePanel() {
  const [width, setWidth] = useState(() =>
    clampPanelWidth(readStoredWidth() ?? PANEL_DEFAULT, window.innerWidth),
  )
  const [dragging, setDragging] = useState(false)
  const drag = useRef<{ startX: number; startWidth: number } | null>(null)

  const commit = useCallback((next: number) => {
    const clamped = clampPanelWidth(next, window.innerWidth)
    setWidth(clamped)
    return clamped
  }, [])

  // A window that shrinks must not leave the sidebar wider than its limit.
  useEffect(() => {
    const onResize = () => setWidth((w) => clampPanelWidth(w, window.innerWidth))
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // While dragging, the whole page shows the resize cursor and text selection
  // is suppressed, or sweeping across the chat highlights everything in it.
  useEffect(() => {
    if (!dragging) return
    const { cursor, userSelect } = document.body.style
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    return () => {
      document.body.style.cursor = cursor
      document.body.style.userSelect = userSelect
    }
  }, [dragging])

  const onPointerDown = useCallback(
    (event: PointerEvent<HTMLElement>) => {
      if (event.button !== 0) return
      event.preventDefault()
      event.currentTarget.setPointerCapture(event.pointerId)
      drag.current = { startX: event.clientX, startWidth: width }
      setDragging(true)
    },
    [width],
  )

  const onPointerMove = useCallback(
    (event: PointerEvent<HTMLElement>) => {
      if (!drag.current) return
      // The panel sits on the right, so dragging the divider LEFT widens it.
      commit(drag.current.startWidth + (drag.current.startX - event.clientX))
    },
    [commit],
  )

  const onPointerUp = useCallback(
    (event: PointerEvent<HTMLElement>) => {
      if (!drag.current) return
      drag.current = null
      setDragging(false)
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId)
      }
      setWidth((w) => {
        writeStoredWidth(w)
        return w
      })
    },
    [],
  )

  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLElement>) => {
      let next: number | null = null
      if (event.key === 'ArrowLeft') next = width + KEY_STEP
      else if (event.key === 'ArrowRight') next = width - KEY_STEP
      else if (event.key === 'Home' || event.key === 'Enter') next = PANEL_DEFAULT
      if (next === null) return
      event.preventDefault()
      writeStoredWidth(commit(next))
    },
    [commit, width],
  )

  const reset = useCallback(() => writeStoredWidth(commit(PANEL_DEFAULT)), [commit])

  return {
    width,
    dragging,
    handleProps: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel: onPointerUp, onKeyDown, onDoubleClick: reset },
  }
}

function readStoredWidth(): number | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    const value = raw === null ? NaN : Number(raw)
    return Number.isFinite(value) ? value : null
  } catch {
    return null
  }
}

function writeStoredWidth(width: number): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, String(width))
  } catch {
    // Storage unavailable (private window, blocked site data). The width still
    // applies for this session; it just will not be remembered.
  }
}
