import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import type { ComponentProps } from 'react'

import { cn } from '@/lib/utils'

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-accent/60 disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        default: 'bg-accent/90 text-background hover:bg-accent',
        secondary: 'bg-surface-raised text-foreground border border-border hover:bg-border/60',
        ghost: 'text-muted hover:bg-surface-raised hover:text-foreground',
        outline: 'border border-border text-foreground hover:bg-surface-raised',
        danger: 'border border-danger/40 bg-danger/10 text-danger hover:bg-danger/20',
      },
      size: {
        default: 'h-9 px-3.5 py-2',
        sm: 'h-7 rounded px-2.5 text-xs',
        icon: 'size-8',
      },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  },
)

export function Button({
  className,
  variant,
  size,
  asChild = false,
  ...props
}: ComponentProps<'button'> & VariantProps<typeof buttonVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot : 'button'
  return <Comp className={cn(buttonVariants({ variant, size }), className)} {...props} />
}
