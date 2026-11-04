import type { ComponentProps } from 'react'

import { PANEL_MIN } from '@/lib/panel-width'
import { cn } from '@/lib/utils'

/**
 * The draggable divider in the gap between two columns — graph and Lumen, or
 * Lumen (or the graph) and the chat. It resizes the column on its right.
 *
 * The whole gap is the hit area, with a grab pill in its middle that lights up
 * on hover: a 1px line is almost impossible to grab, and an invisible one is
 * never found. It is a focusable `separator`, so the width can also be changed
 * from the keyboard (arrow keys; Enter or Home to reset), and a double-click
 * resets it.
 */
export function PanelResizer({
  width,
  dragging,
  label,
  ...handlers
}: { width: number; dragging: boolean; label: string } & ComponentProps<'div'>) {
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={width}
      aria-valuemin={PANEL_MIN}
      tabIndex={0}
      title="Drag to resize · double-click to reset"
      className="group relative flex w-3 shrink-0 cursor-col-resize touch-none items-center justify-center outline-none"
      {...handlers}
    >
      <span
        className={cn(
          'h-12 w-1 rounded-full bg-white/10 transition-all duration-200',
          'group-hover:h-20 group-hover:bg-accent/70 group-focus-visible:h-20 group-focus-visible:bg-accent',
          dragging && 'h-24 bg-accent',
        )}
      />
    </div>
  )
}
