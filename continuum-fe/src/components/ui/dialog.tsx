import * as DialogPrimitive from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import type { ComponentProps } from 'react'

import { cn } from '@/lib/utils'

export const Dialog = DialogPrimitive.Root
export const DialogTrigger = DialogPrimitive.Trigger

export function DialogContent({
  className,
  children,
  ...props
}: ComponentProps<typeof DialogPrimitive.Content>) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/60 backdrop-blur-[2px]" />
      <DialogPrimitive.Content
        className={cn(
          'panel fixed top-1/2 left-1/2 z-50 flex max-h-[85vh] w-[calc(100vw-2rem)] max-w-lg',
          '-translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden outline-none',
          className,
        )}
        {...props}
      >
        {children}
        <DialogPrimitive.Close
          className="absolute top-3 right-3 rounded p-1 text-muted hover:bg-surface-raised hover:text-foreground"
          aria-label="Close"
        >
          <X className="size-4" />
        </DialogPrimitive.Close>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  )
}

export function DialogTitle({ className, ...props }: ComponentProps<typeof DialogPrimitive.Title>) {
  return (
    <DialogPrimitive.Title
      className={cn('px-4 pt-3.5 text-sm font-semibold tracking-tight', className)}
      {...props}
    />
  )
}

export function DialogDescription({
  className,
  ...props
}: ComponentProps<typeof DialogPrimitive.Description>) {
  return (
    <DialogPrimitive.Description
      className={cn('px-4 pt-1 text-xs leading-relaxed text-muted', className)}
      {...props}
    />
  )
}
