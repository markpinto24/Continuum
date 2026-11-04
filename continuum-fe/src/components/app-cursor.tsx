import { useEffect, useRef, useState } from 'react'

import { useMediaQuery } from '@/hooks/use-media-query'

/** What the pointer is over: something to press, something to type in, or neither. */
type Over = 'none' | 'action' | 'text'

/** Pressable things: the ring grows and fills over them. */
const ACTION =
  'a[href], button:not(:disabled), [role="button"], [role="tab"], [role="radio"], [role="menuitem"], [role="menuitemcheckbox"], [role="option"], select, summary, label[for], canvas'
/** Places where the native cursor says more than ours: the text caret, the resize arrows. */
const NATIVE = 'input:not([type="checkbox"]):not([type="radio"]):not([type="range"]), textarea, [contenteditable="true"], [role="separator"]'

const FINE_POINTER = '(pointer: fine)'
const REDUCED_MOTION = '(prefers-reduced-motion: reduce)'

/**
 * The cursor, in the project's own light: a small dot exactly where the pointer
 * is and a soft ring trailing it, both in the mode's accent (cyan for Work, rose
 * for Personal — `--color-accent`). Over anything pressable the ring grows and
 * fills a little; pressing shrinks it. Over a text field or a column divider it
 * steps aside for the native caret or resize arrows, which say more.
 *
 * Only with a fine pointer — never on a touch screen, which has no cursor — and
 * without the trailing motion when motion is reduced. It moves by transform
 * alone, written to the DOM from one animation loop, never through React state.
 */
export function AppCursor() {
  const fine = useMediaQuery(FINE_POINTER)
  const reduced = useMediaQuery(REDUCED_MOTION)
  const dot = useRef<HTMLDivElement | null>(null)
  const ring = useRef<HTMLDivElement | null>(null)
  const [over, setOver] = useState<Over>('none')
  const [pressed, setPressed] = useState(false)
  const [shown, setShown] = useState(false)

  useEffect(() => {
    if (!fine) return
    const root = document.documentElement
    root.classList.add('has-app-cursor')

    const pointer = { x: -100, y: -100 }
    const trail = { x: -100, y: -100 }
    let frame = 0
    let last = performance.now()

    const place = (el: HTMLDivElement | null, x: number, y: number) => {
      if (el) el.style.transform = `translate3d(${x}px, ${y}px, 0)`
    }
    const tick = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000)
      last = now
      // Frame-rate independent easing: the ring trails by the same feel at any refresh rate.
      const follow = reduced ? 1 : 1 - Math.exp(-dt * 18)
      trail.x += (pointer.x - trail.x) * follow
      trail.y += (pointer.y - trail.y) * follow
      place(ring.current, trail.x, trail.y)
      frame = requestAnimationFrame(tick)
    }

    const onMove = (event: PointerEvent) => {
      // A finger has no cursor. (A browser always says which; an unsaid one is a mouse.)
      if (event.pointerType === 'touch') return
      pointer.x = event.clientX
      pointer.y = event.clientY
      place(dot.current, pointer.x, pointer.y)
      setShown(true)
    }
    const onOver = (event: PointerEvent) => {
      const target = event.target instanceof Element ? event.target : null
      setOver(target?.closest(NATIVE) ? 'text' : target?.closest(ACTION) ? 'action' : 'none')
    }
    const onDown = () => setPressed(true)
    const onUp = () => setPressed(false)
    const onLeave = () => setShown(false)

    window.addEventListener('pointermove', onMove, { passive: true })
    window.addEventListener('pointerover', onOver, { passive: true })
    window.addEventListener('pointerdown', onDown, { passive: true })
    window.addEventListener('pointerup', onUp, { passive: true })
    document.documentElement.addEventListener('pointerleave', onLeave)
    frame = requestAnimationFrame(tick)
    return () => {
      cancelAnimationFrame(frame)
      root.classList.remove('has-app-cursor')
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerover', onOver)
      window.removeEventListener('pointerdown', onDown)
      window.removeEventListener('pointerup', onUp)
      document.documentElement.removeEventListener('pointerleave', onLeave)
    }
  }, [fine, reduced])

  if (!fine) return null
  const visible = shown && over !== 'text'
  const ringScale = pressed ? 0.75 : over === 'action' ? 1.55 : 1

  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 z-[9999]">
      <div ref={ring} className="absolute top-0 left-0 will-change-transform">
        <div
          className="-mt-4 -ml-4 size-8 rounded-full border transition-[transform,opacity,background-color,border-color] duration-200 ease-out"
          style={{
            opacity: visible ? 1 : 0,
            transform: `scale(${ringScale})`,
            // Lighter over something pressable: the primary buttons are the accent
            // itself, and an accent ring on them disappeared.
            borderColor:
              over === 'action'
                ? 'color-mix(in oklch, white 65%, var(--color-accent))'
                : 'color-mix(in oklch, var(--color-accent) 70%, transparent)',
            backgroundColor:
              over === 'action'
                ? 'color-mix(in oklch, var(--color-accent) 12%, transparent)'
                : 'transparent',
            boxShadow: '0 0 14px -2px color-mix(in oklch, var(--color-accent) 60%, transparent)',
          }}
        />
      </div>
      <div ref={dot} className="absolute top-0 left-0 will-change-transform">
        <div
          className="-mt-[3px] -ml-[3px] size-1.5 rounded-full transition-opacity duration-150"
          style={{
            opacity: visible ? 1 : 0,
            backgroundColor: 'var(--color-accent)',
            // A thin dark edge keeps the dot visible on accent-coloured buttons.
            boxShadow: '0 0 0 1.5px rgb(0 0 0 / 0.55), 0 0 8px var(--color-accent)',
          }}
        />
      </div>
    </div>
  )
}
