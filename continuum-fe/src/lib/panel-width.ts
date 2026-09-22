/** Sidebar width limits, in CSS pixels. */
export const PANEL_MIN = 320
export const PANEL_DEFAULT = 416
const PANEL_ABSOLUTE_MAX = 1100

/**
 * Keep the sidebar usable and the graph visible.
 *
 * The graph is the primary view, so the sidebar may take at most 60% of the
 * window — dragging it wider would leave a canvas too narrow to orbit. Below
 * PANEL_MIN the chat input and the inbox's verdict buttons stop fitting.
 */
export function clampPanelWidth(width: number, viewport: number): number {
  const max = Math.max(PANEL_MIN, Math.min(PANEL_ABSOLUTE_MAX, Math.floor(viewport * 0.6)))
  if (!Number.isFinite(width)) return Math.min(PANEL_DEFAULT, max)
  return Math.round(Math.min(max, Math.max(PANEL_MIN, width)))
}
