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
