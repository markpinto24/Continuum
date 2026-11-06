import { useEffect, useRef } from 'react'

import { useMediaQuery } from '@/hooks/use-media-query'

/** Pressable things: the ring wraps a small one and grows over a large one. */
const ACTION =
  'a[href], button:not(:disabled), [role="button"], [role="tab"], [role="radio"], [role="menuitem"], [role="menuitemcheckbox"], [role="option"], select, summary, label[for], canvas'
/** Places where the native cursor says more than ours: the text caret, the resize arrows. */
const NATIVE = 'input:not([type="checkbox"]):not([type="radio"]):not([type="range"]), textarea, [contenteditable="true"], [role="separator"]'

/** Wrap only what is button-sized; hugging a whole canvas or a long row would just be a frame. */
const WRAP_MAX = { width: 360, height: 96 }
const WRAP_PAD = 5
const RING = 34
const RING_OVER = 46

/** Dots in the comet tail. */
const TAIL = 14

const FINE_POINTER = '(pointer: fine)'
const REDUCED_MOTION = '(prefers-reduced-motion: reduce)'

/**
 * The cursor, in the project's own light (`--color-accent`: cyan in Work, rose
 * in Personal):
 *
 * - a glowing core exactly where the pointer is, with a short comet tail as it moves;
 * - a ring trailing it with light running round its edge — the sign-in card's
 *   edge light, in miniature;
 * - over a button, the ring lets go of the pointer and wraps the button in its
 *   own shape, leaning a little toward the pointer; over something larger (the
 *   graph) it grows instead;
 * - a ripple on every press.
 *
 * Over a text field or a column divider it steps aside for the native caret or
 * resize arrows, which say more. Only with a fine pointer — never on touch —
 * and with reduced motion it follows exactly: no tail, no spin, no ripple.
 *
 * Everything moves by writing styles from one animation loop; state lives in
 * data attributes on its root, read by CSS (`.app-cursor` in index.css), so a
 * hover never re-renders React.
 */
export function AppCursor() {
  const fine = useMediaQuery(FINE_POINTER)
  const reduced = useMediaQuery(REDUCED_MOTION)
  const root = useRef<HTMLDivElement | null>(null)
  const ring = useRef<HTMLDivElement | null>(null)
  const dot = useRef<HTMLDivElement | null>(null)
  const tail = useRef<(HTMLDivElement | null)[]>([])

  useEffect(() => {
    const host = root.current
    if (!fine || !host) return
    const html = document.documentElement
    html.classList.add('has-app-cursor')

    const pointer = { x: -100, y: -100 }
    const box = { x: -100, y: -100, w: RING, h: RING, r: RING / 2 }
    const dots = Array.from({ length: TAIL }, () => ({ x: -100, y: -100 }))
    let over: 'none' | 'action' | 'text' = 'none'
    let wrapped: Element | null = null
    let pressed = false
    let frame = 0
    let last = performance.now()

    const set = (key: string, value: string) => {
      if (host.dataset[key] !== value) host.dataset[key] = value
    }

    const tick = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000)
      last = now
      // Frame-rate independent easing: the same feel at 60 Hz and 144 Hz.
      const ease = (rate: number) => (reduced ? 1 : 1 - Math.exp(-dt * rate))

      // Where the ring wants to be: wrapped round a button, or round the pointer.
      if (wrapped && !wrapped.isConnected) {
        wrapped = null
        set('wrap', 'false')
      }
      let target: typeof box
      if (wrapped) {
        const rect = wrapped.getBoundingClientRect()
        const radius = parseFloat(getComputedStyle(wrapped).borderTopLeftRadius) || 8
        // A press squeezes the wrap in, as a press squeezes a button.
        const pad = pressed ? 2 : WRAP_PAD
        const h = rect.height + pad * 2
        // Lean toward the pointer, so the wrap still feels held, not parked.
        const leanX = (pointer.x - (rect.left + rect.width / 2)) * 0.08
        const leanY = (pointer.y - (rect.top + rect.height / 2)) * 0.12
        target = {
          x: rect.left - pad + leanX,
          y: rect.top - pad + leanY,
          w: rect.width + pad * 2,
          h,
          r: Math.min(radius + pad, h / 2),
        }
      } else {
        const size = (over === 'action' ? RING_OVER : RING) * (pressed ? 0.82 : 1)
        target = { x: pointer.x - size / 2, y: pointer.y - size / 2, w: size, h: size, r: size / 2 }
      }

      const move = ease(16)
      const shape = ease(22)
      box.x += (target.x - box.x) * move
      box.y += (target.y - box.y) * move
      box.w += (target.w - box.w) * shape
      box.h += (target.h - box.h) * shape
      box.r += (target.r - box.r) * shape
      const el = ring.current
      if (el) {
        el.style.transform = `translate3d(${box.x}px, ${box.y}px, 0)`
        el.style.width = `${box.w}px`
        el.style.height = `${box.h}px`
        el.style.borderRadius = `${box.r}px`
      }

      // The tail: each dot chases the one before it, so a fast move draws a streak
      // and a still pointer gathers it back under the core.
      if (!reduced) {
        let lead = pointer
        const chase = ease(36)
        dots.forEach((d, i) => {
          d.x += (lead.x - d.x) * chase
          d.y += (lead.y - d.y) * chase
          const node = tail.current[i]
          if (node) node.style.transform = `translate3d(${d.x}px, ${d.y}px, 0)`
          lead = d
        })
      }
      frame = requestAnimationFrame(tick)
    }

    const onMove = (event: PointerEvent) => {
      // A finger has no cursor. (A browser always says which; an unsaid one is a mouse.)
      if (event.pointerType === 'touch') return
      pointer.x = event.clientX
      pointer.y = event.clientY
      if (dot.current) dot.current.style.transform = `translate3d(${pointer.x}px, ${pointer.y}px, 0)`
      set('shown', 'true')
    }
    const onOver = (event: PointerEvent) => {
      const target = event.target instanceof Element ? event.target : null
      const action = target?.closest(ACTION) ?? null
      over = target?.closest(NATIVE) ? 'text' : action ? 'action' : 'none'
      set('over', over)
      wrapped = null
      if (over === 'action' && action && action.tagName !== 'CANVAS') {
        const rect = action.getBoundingClientRect()
        if (rect.width > 0 && rect.width <= WRAP_MAX.width && rect.height <= WRAP_MAX.height) {
          wrapped = action
        }
      }
      set('wrap', wrapped ? 'true' : 'false')
    }
    const onDown = (event: PointerEvent) => {
      if (event.pointerType === 'touch') return
      pressed = true
      set('pressed', 'true')
      if (reduced || over === 'text') return
      const ripple = document.createElement('div')
      ripple.className = 'app-cursor-ripple'
      // `translate`, not `transform`: the ripple animates `scale`, which would
      // otherwise scale the position too and play it up and to the left.
      ripple.style.translate = `${event.clientX}px ${event.clientY}px`
      ripple.addEventListener('animationend', () => ripple.remove(), { once: true })
      host.appendChild(ripple)
    }
    const onUp = () => {
      pressed = false
      set('pressed', 'false')
    }
    const onLeave = () => set('shown', 'false')

    window.addEventListener('pointermove', onMove, { passive: true })
    window.addEventListener('pointerover', onOver, { passive: true })
    window.addEventListener('pointerdown', onDown, { passive: true })
    window.addEventListener('pointerup', onUp, { passive: true })
    html.addEventListener('pointerleave', onLeave)
    frame = requestAnimationFrame(tick)
    return () => {
      cancelAnimationFrame(frame)
      html.classList.remove('has-app-cursor')
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerover', onOver)
      window.removeEventListener('pointerdown', onDown)
      window.removeEventListener('pointerup', onUp)
      html.removeEventListener('pointerleave', onLeave)
    }
  }, [fine, reduced])

  if (!fine) return null

  return (
    <div
      ref={root}
      aria-hidden
      className="app-cursor pointer-events-none fixed inset-0 z-[9999]"
      data-shown="false"
      data-over="none"
      data-wrap="false"
      data-pressed="false"
    >
      {!reduced &&
        Array.from({ length: TAIL }, (_, i) => {
          const left = 1 - i / TAIL
          return (
            <div
              key={i}
              ref={(node) => {
                tail.current[i] = node
              }}
              className="app-cursor-tail"
            >
              <span style={{ scale: `${0.25 + 0.75 * left}`, opacity: 0.55 * left * left }} />
            </div>
          )
        })}
      <div ref={ring} className="app-cursor-ring" />
      <div ref={dot} className="app-cursor-dot">
        <span />
      </div>
    </div>
  )
}
