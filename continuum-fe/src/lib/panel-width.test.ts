import { describe, expect, it } from 'vitest'

import { PANEL_DEFAULT, PANEL_MIN, clampPanelWidth } from './panel-width'

describe('clampPanelWidth', () => {
  it('never lets the sidebar take more than 60% of the window', () => {
    // The graph is the primary view; it must keep room to be orbited.
    expect(clampPanelWidth(5000, 1600)).toBe(960)
  })

  it('never shrinks below the minimum that fits the chat input', () => {
    expect(clampPanelWidth(10, 1600)).toBe(PANEL_MIN)
  })

  it('keeps the minimum even on a window too narrow for the 60% rule', () => {
    expect(clampPanelWidth(900, 400)).toBe(PANEL_MIN)
  })

  it('falls back to the default for a corrupt stored value', () => {
    expect(clampPanelWidth(Number.NaN, 1600)).toBe(PANEL_DEFAULT)
  })

  it('passes a sensible width through unchanged', () => {
    expect(clampPanelWidth(500, 1600)).toBe(500)
  })
})
