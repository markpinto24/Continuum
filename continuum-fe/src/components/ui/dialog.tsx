import * as DialogPrimitive from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { createContext, useContext, useRef, type ComponentProps } from 'react'

import { AT_REST, gsap, MOTION, reducedMotion, useGSAP, usePresence } from '@/lib/motion'
import { cn } from '@/lib/utils'

/** Whether the dialog is open, and whether it is still on screen leaving. */
const Presence = createContext<{ open: boolean; present: boolean; exited: () => void }>({
  open: false,
  present: false,
  exited: () => {},
})

/**
 * A controlled dialog that animates out as well as in. Radix unmounts the
 * content the moment it closes, so it is force-mounted here and removed only
 * when the exit animation has played.
 */
export function Dialog({
  open,
  onOpenChange,
  children,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  children: React.ReactNode
}) {
  const { present, exited } = usePresence(open)
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <Presence.Provider value={{ open, present, exited }}>{children}</Presence.Provider>
    </DialogPrimitive.Root>
  )
}

export function DialogContent(props: ComponentProps<typeof DialogPrimitive.Content>) {
  const { present } = useContext(Presence)
  return present ? (
    <DialogPrimitive.Portal forceMount>
      <AnimatedContent {...props} />
    </DialogPrimitive.Portal>
  ) : null
}

/**
 * Inside the portal, not around it: Radix's Portal renders nothing on its first
 * pass, so an animation started outside it would find no element to move.
 */
function AnimatedContent({
  className,
  children,
  // The portal hands its child a ref of its own (a prop, in React 19); spread
  // onto the panel it would replace the one the animation needs.
  ref: _portalRef,
  ...props
}: ComponentProps<typeof DialogPrimitive.Content>) {
  const { open, exited } = useContext(Presence)
  const overlay = useRef<HTMLDivElement>(null)
  const panel = useRef<HTMLDivElement>(null)

  useGSAP(
    () => {
      const content = panel.current
      if (!content) return
      const parts = [overlay.current, content]
      if (reducedMotion()) {
        gsap.set(parts, { clearProps: AT_REST })
        if (!open) exited()
        return
      }
      gsap.killTweensOf(parts)
      if (open) {
        gsap.fromTo(
          overlay.current,
          { opacity: 0 },
          { opacity: 1, duration: 0.3, ease: 'power1.out', clearProps: AT_REST },
        )
        // The panel itself carries the glass, so fading it never fades a blur's
        // backdrop away (an ancestor below full opacity would).
        gsap.fromTo(
          content,
          { opacity: 0, y: 18, scale: 0.97 },
          { opacity: 1, y: 0, scale: 1, ...MOTION.enter, clearProps: AT_REST },
        )
      } else {
        gsap.to(overlay.current, { opacity: 0, ...MOTION.exit })
        gsap.to(content, { opacity: 0, y: 10, scale: 0.98, ...MOTION.exit, onComplete: exited })
      }
    },
    { dependencies: [open] },
  )

  return (
    <>
      <DialogPrimitive.Overlay
        ref={overlay}
        forceMount
        className={cn('fixed inset-0 z-40 bg-black/55 backdrop-blur-sm', !open && 'pointer-events-none')}
      />
      <DialogPrimitive.Content
        ref={panel}
        forceMount
        className={cn(
          'fixed top-1/2 left-1/2 z-50 flex max-h-[85vh] w-[calc(100vw-2rem)] max-w-lg',
          '-translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-border',
          'bg-surface shadow-2xl shadow-black/50 outline-none',
          !open && 'pointer-events-none',
          className,
        )}
        {...props}
      >
        {children}
        <DialogPrimitive.Close
          className="absolute top-3.5 right-3.5 rounded-lg p-1.5 text-muted transition-colors hover:bg-surface-raised hover:text-foreground"
          aria-label="Close"
        >
          <X className="size-4" />
        </DialogPrimitive.Close>
      </DialogPrimitive.Content>
    </>
  )
}

export function DialogTitle({ className, ...props }: ComponentProps<typeof DialogPrimitive.Title>) {
  return (
    <DialogPrimitive.Title
      className={cn('px-5 pt-5 text-base font-semibold tracking-tight', className)}
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
      className={cn('px-5 pt-1 text-xs leading-relaxed text-muted', className)}
      {...props}
    />
  )
}
