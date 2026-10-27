import { useEffect, useState } from 'react'

/**
 * Whether a CSS media query matches, kept up to date. For layout that CSS alone
 * cannot express — the floating panel's measured width feeds the graph's view
 * offset, so it has to be known in JavaScript too.
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => matchesNow(query))
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return // jsdom
    const list = window.matchMedia(query)
    const update = () => setMatches(list.matches)
    update()
    list.addEventListener('change', update)
    return () => list.removeEventListener('change', update)
  }, [query])
  return matches
}

function matchesNow(query: string): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia(query).matches
    : false
}

/** Phones, and narrow windows: the panel becomes a full-width sheet. */
export const NARROW = '(max-width: 640px)'
