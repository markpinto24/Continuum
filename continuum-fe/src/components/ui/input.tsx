import type { ComponentProps } from 'react'

import { cn } from '@/lib/utils'

export function Input({ className, ...props }: ComponentProps<'input'>) {
  return (
    <input
      className={cn(
        'h-10 w-full rounded-lg border border-border bg-background/60 px-3 text-sm transition-colors',
        'placeholder:text-muted/60 outline-none hover:border-muted/40',
        'focus-visible:border-accent/60 focus-visible:ring-3 focus-visible:ring-accent/15',
        'disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
    />
  )
}
