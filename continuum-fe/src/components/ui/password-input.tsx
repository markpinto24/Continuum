import { Eye, EyeOff } from 'lucide-react'
import { type ComponentProps, useState } from 'react'

import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

/**
 * A password input with a show/hide toggle.
 *
 * The toggle is a real button — focusable, `aria-pressed`, named for what it
 * will do — and `type="button"` so pressing it never submits the form. Hidden
 * is the default and the state is per field, so revealing the password does not
 * also reveal its confirmation.
 */
export function PasswordInput({ className, ...props }: Omit<ComponentProps<'input'>, 'type'>) {
  const [visible, setVisible] = useState(false)

  return (
    <div className="relative">
      <Input
        {...props}
        type={visible ? 'text' : 'password'}
        // Room for the button, and no second eye from Edge's built-in reveal.
        className={cn('pr-9 [&::-ms-reveal]:hidden', className)}
      />
      <button
        type="button"
        onClick={() => setVisible((shown) => !shown)}
        aria-label={visible ? 'Hide password' : 'Show password'}
        aria-pressed={visible}
        disabled={props.disabled}
        className={cn(
          'absolute inset-y-0 right-0 flex w-8 items-center justify-center rounded-r-md',
          'text-muted transition-colors hover:text-foreground',
          'outline-none focus-visible:ring-2 focus-visible:ring-accent/50',
          'disabled:pointer-events-none disabled:opacity-50',
        )}
      >
        {visible ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
      </button>
    </div>
  )
}
