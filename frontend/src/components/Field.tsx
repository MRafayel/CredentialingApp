import { useId, type ComponentProps, type ReactNode } from 'react'
import { VerdictReview } from '@/components/illustrations'

/**
 * The shared input: 44 px with a hairline. Focus is the one global ring (index.css): a 2 px
 * accent outline outside the border, which itself does not change colour.
 */
export const INPUT =
  'h-11 min-w-0 rounded-control border border-line-strong bg-surface px-3 text-body text-ink transition-colors placeholder:text-ink-muted hover:border-ink-faint'

/** The same input as a pill, for search fields. */
export const PILL_INPUT =
  'h-11 min-w-0 rounded-full border border-line-strong bg-surface px-3 text-body text-ink transition-colors placeholder:text-ink-muted hover:border-ink-faint'

/** A labelled input with an optional hint; the hint can turn into a warning. */
export function Field({
  label,
  hint,
  warning,
  className,
  inputClassName,
  ...input
}: {
  label: string
  hint?: ReactNode
  warning?: ReactNode
  className?: string
  inputClassName?: string
} & ComponentProps<'input'>) {
  const id = useId()
  const hintId = `${id}-hint`
  const message = warning ?? hint
  return (
    <div className={`flex min-w-0 flex-col gap-1.5 ${className ?? ''}`}>
      <label htmlFor={id} className="text-small font-medium text-ink-muted">
        {label}
      </label>
      <input
        id={id}
        aria-describedby={message ? hintId : undefined}
        className={`${INPUT} w-full ${warning ? 'border-status-review' : ''} ${inputClassName ?? ''}`}
        {...input}
      />
      {message && (
        <p
          id={hintId}
          className={`flex items-center gap-1.5 text-small ${warning ? 'font-medium text-ink' : 'text-ink-muted'}`}
        >
          {warning && <VerdictReview size={14} className="shrink-0" />}
          {message}
        </p>
      )}
    </div>
  )
}
