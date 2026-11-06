import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import { Flip } from 'gsap/Flip'
import { useCallback, useState } from 'react'

/**
 * GSAP, registered once. It animates what CSS cannot do well: a layout change
 * (Flip — the columns), something leaving before it unmounts (sheets, dialogs)
 * and a sequence (the sign-in page's entrance). What draws itself every frame —
 * the graph, Lumen's orb and waveform, the aurora, the cursor — keeps its own
 * loop: GSAP tweens values, it does not draw.
 *
 * `useGSAP` rather than `useEffect`: it reverts what it started on unmount, so
 * StrictMode's rehearsal mount leaves no half-played tween behind.
 */
gsap.registerPlugin(Flip, useGSAP)
// An empty target list is normal here — no hero on a phone, no divider with
// one column — and GSAP would warn about each one.
gsap.config({ nullTargetWarn: false })

export { Flip, gsap, useGSAP }

/** One feel everywhere: things arrive fast and settle, and leave quicker than they came. */
export const MOTION = {
  enter: { duration: 0.45, ease: 'power3.out' },
  exit: { duration: 0.2, ease: 'power2.in' },
  layout: { duration: 0.5, ease: 'power3.inOut' },
} as const

/**
 * What an entrance clears when it lands, so at rest an element is exactly what
 * its CSS says: a transform left behind would make it the containing block of
 * anything fixed inside it, and `translate: none` would cancel Tailwind's own.
 */
export const AT_REST = 'transform,translate,rotate,scale,opacity'

/** Asked at the moment of animating, so a change of the setting applies at once. */
export function reducedMotion(): boolean {
  return (
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )
}

/**
 * Keeps something mounted while it animates out. `present` stays true after
 * `open` turns false, until the exit animation calls `exited`; opening again
 * mid-exit simply plays the entrance from wherever it is.
 */
export function usePresence(open: boolean): { present: boolean; exited: () => void } {
  const [leaving, setLeaving] = useState(false)
  const [wasOpen, setWasOpen] = useState(open)
  // Derived during render (React's pattern for state from a prop change), so
  // the closing frame never renders without the element.
  if (open !== wasOpen) {
    setWasOpen(open)
    setLeaving(!open)
  }
  const exited = useCallback(() => setLeaving(false), [])
  return { present: open || leaving, exited }
}
