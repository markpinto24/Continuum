import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import type { ComponentProps } from 'react'

import { cn } from '@/lib/utils'

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-medium transition-all duration-150 outline-none select-none focus-visible:ring-2 focus-visible:ring-accent/50 focus-visible:ring-offset-2 focus-visible:ring-offset-background active:scale-[0.98] disabled:pointer-events-none disabled:opacity-45 [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        default:
          'bg-accent text-background shadow-[0_0_0_1px_color-mix(in_oklch,var(--color-accent)_40%,transparent),0_6px_20px_-8px_var(--color-accent)] hover:brightness-110',
        secondary: 'bg-surface-raised text-foreground hover:bg-border/70',
        ghost: 'text-muted hover:bg-surface-raised hover:text-foreground',
        outline: 'border border-border text-foreground hover:bg-surface-raised',
        danger: 'bg-danger/12 text-danger hover:bg-danger/20',
      },
      size: {
        default: 'h-9 px-4',
        sm: 'h-7 rounded-md px-2.5 text-xs',
        icon: 'size-8 rounded-lg',
        lg: 'h-11 px-5 text-[15px]',
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
