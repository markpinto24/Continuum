import { cn } from '@/lib/utils'

/**
 * The Continuum mark: three beliefs and the edges between them, in a ring.
 * Drawn in currentColor so it takes whatever accent it sits in.
 */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" fill="none" aria-hidden className={cn('size-6', className)}>
      <circle cx="16" cy="16" r="14" stroke="currentColor" strokeOpacity="0.35" strokeWidth="1.5" />
      <path
        d="M10 20.5 16 9.5l6 11z"
        stroke="currentColor"
        strokeOpacity="0.7"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      <circle cx="16" cy="9.5" r="2.6" fill="currentColor" />
      <circle cx="10" cy="20.5" r="2.6" fill="currentColor" fillOpacity="0.85" />
      <circle cx="22" cy="20.5" r="2.6" fill="currentColor" fillOpacity="0.6" />
    </svg>
  )
}

/** The mark on its tile — the app icon, as used in the header. */
export function BrandTile({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        'relative flex size-8 items-center justify-center rounded-[10px] bg-gradient-to-b from-[#12222f] to-[#0a141c] ring-1 ring-accent/30 shadow-[0_0_18px_-6px_var(--color-accent)]',
        className,
      )}
    >
      <BrandMark className="size-5 text-accent" />
    </span>
  )
}

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn('flex items-center gap-2.5', className)}>
      <BrandTile />
      <span className="text-[15px] font-semibold tracking-tight">Continuum</span>
    </span>
  )
}
