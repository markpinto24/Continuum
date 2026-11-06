import { cn } from '@/lib/utils'

/**
 * A column's name in Lumen's voice — wide-tracked capitals and a small pill in
 * the mode's accent — so Memories, Lumen and Chat read as one set of screens.
 */
export function ColumnHeading({
  title,
  tag,
  className,
}: {
  title: string
  /** Short state, shown in the pill: "9 beliefs", "personal · only you". */
  tag?: string
  className?: string
}) {
  return (
    <div className={cn('flex min-w-0 items-center gap-3', className)}>
      <h2 className="text-[13px] font-semibold tracking-[0.35em] text-foreground/90 uppercase">
        {title}
      </h2>
      {tag && (
        <span className="flex min-w-0 items-center gap-1.5 truncate rounded-full bg-accent/[0.07] px-2.5 py-0.5 font-mono text-[10px] tracking-[0.18em] text-accent uppercase ring-1 ring-accent/30 ring-inset">
          <span className="size-1.5 shrink-0 rounded-full bg-accent" />
          <span className="truncate">{tag}</span>
        </span>
      )}
    </div>
  )
}
