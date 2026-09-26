import { type ReactElement, cloneElement, useId } from 'react'

import { cn } from '@/lib/utils'

/**
 * A labelled form control, with an optional hint and a live error.
 *
 * The label points at the control by id rather than wrapping it. Wrapping
 * would fold every button inside the control — a password field's show/hide
 * toggle — into the control's accessible name ("Password Show password").
 * Hint and error are wired to `aria-describedby`, so a screen reader reads them
 * with the field, and an error also sets `aria-invalid`.
 */
export function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string
  hint?: string
  /** Shown instead of the hint, in the danger colour, while the value is wrong. */
  error?: string | null
  children: ReactElement<{ id?: string; 'aria-describedby'?: string; 'aria-invalid'?: boolean }>
}) {
  const id = useId()
  const noteId = `${id}-note`
  const note = error || hint

  const control = cloneElement(children, {
    id: children.props.id ?? id,
    'aria-describedby': note ? noteId : undefined,
    'aria-invalid': error ? true : undefined,
  })

  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={children.props.id ?? id} className="text-[11px] font-medium text-muted">
        {label}
      </label>
      {control}
      {note && (
        <span
          id={noteId}
          // Polite: announced when the user pauses typing, not on every keystroke.
          aria-live={error ? 'polite' : undefined}
          className={cn(
            'text-[11px] leading-relaxed',
            error ? 'text-danger' : 'text-muted/70',
          )}
        >
          {note}
        </span>
      )}
    </div>
  )
}
