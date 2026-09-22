import type { ComponentProps } from 'react'

import { PANEL_MIN } from '@/lib/panel-width'
import { cn } from '@/lib/utils'

/**
 * The draggable divider between graph and sidebar.
 *
 * The visible line is 1px but the hit area is 8px, centred on it: a 1px target
 * is almost impossible to grab. It is a focusable `separator` so the width can
 * also be changed from the keyboard (arrow keys; Enter or Home to reset), and
 * double-clicking resets it.
 */
export function PanelResizer({
  width,
  dragging,
  ...handlers
}: { width: number; dragging: boolean } & ComponentProps<'div'>) {
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize sidebar"
      aria-valuenow={width}
      aria-valuemin={PANEL_MIN}
      tabIndex={0}
      title="Drag to resize · double-click to reset"
      className="group relative z-10 -mx-1 w-2 shrink-0 cursor-col-resize touch-none outline-none"
      {...handlers}
    >
      <span
        className={cn(
          'absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-border transition-colors',
          'group-hover:bg-accent/60 group-focus-visible:bg-accent',
          dragging && 'bg-accent',
        )}
      />
    </div>
  )
}
