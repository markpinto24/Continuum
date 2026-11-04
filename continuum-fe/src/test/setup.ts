import '@testing-library/react'

/**
 * jsdom has no ResizeObserver, and the graph container measures itself with one.
 * A no-op keeps component tests from throwing before they reach an assertion.
 */
class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver ??= NoopResizeObserver as unknown as typeof ResizeObserver

/**
 * jsdom has no IntersectionObserver either; React Bits' ShinyText (Lumen's
 * waiting line) starts one when it mounts.
 */
class NoopIntersectionObserver {
  readonly root = null
  readonly rootMargin = ''
  readonly thresholds = []
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return []
  }
}

globalThis.IntersectionObserver ??= NoopIntersectionObserver as unknown as typeof IntersectionObserver

/**
 * jsdom has no canvas, and says so on stderr for every call. Null is what a
 * browser without WebGL returns; Aurora (Lumen's backdrop) then draws nothing,
 * and ShinyText falls back to its plain colour.
 */
HTMLCanvasElement.prototype.getContext = (() => null) as unknown as HTMLCanvasElement['getContext']
