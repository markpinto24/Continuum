import { useRef } from 'react'

import { gsap, reducedMotion, useGSAP } from '@/lib/motion'
import { cn } from '@/lib/utils'

/**
 * A line that arrives word by word, each one falling into place out of a blur —
 * Lumen's greeting. It replaced React Bits' BlurText, which brought in a second
 * animation library (Motion) for this one effect; the look is the same: from
 * 10px blur and 50px above, past a small overshoot, to rest.
 *
 * The words are drawn for the eye only; a screen reader gets the line once,
 * whole. With reduced motion it is simply there.
 */
export function BlurWords({
  text,
  className,
  color,
  stagger = 0.07,
}: {
  text: string
  className?: string
  color?: string
  /** Seconds between one word and the next. */
  stagger?: number
}) {
  const root = useRef<HTMLParagraphElement>(null)
  const words = text.split(' ')

  useGSAP(
    () => {
      if (reducedMotion()) return
      gsap.fromTo(
        root.current?.querySelectorAll('[data-word]') ?? [],
        { opacity: 0, y: -50, filter: 'blur(10px)' },
        {
          keyframes: [
            { opacity: 0.5, y: 5, filter: 'blur(5px)', duration: 0.35, ease: 'none' },
            { opacity: 1, y: 0, filter: 'blur(0px)', duration: 0.35, ease: 'power2.out' },
          ],
          stagger,
          // No filter left on the words once they rest: a filtered element is
          // its own compositing layer for no reason.
          clearProps: 'filter',
        },
      )
    },
    { scope: root, dependencies: [text] },
  )

  return (
    <p ref={root} className={cn('flex flex-wrap', className)} style={{ color }}>
      <span className="sr-only">{text}</span>
      {words.map((word, i) => (
        <span
          key={i}
          aria-hidden
          data-word
          className="inline-block will-change-[transform,filter,opacity]"
        >
          {i < words.length - 1 ? `${word}\u00A0` : word}
        </span>
      ))}
    </p>
  )
}
