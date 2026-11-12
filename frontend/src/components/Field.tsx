import { useId, type ComponentProps, type ReactNode } from 'react'
import { VerdictReview } from '@/components/illustrations'

/**
 * The shared input: 44 px with a hairline. Focus is the one global ring (index.css): a 2 px
 * accent outline outside the border, which itself does not change colour.
 */
export const INPUT =
  'h-11 min-w-0 rounded-control border border-line-strong bg-surface px-3 text-body text-ink transition-colors placeholder:text-ink-muted hover:border-ink-faint'

/**
 * A page's own search (Lookup, Providers): the pill one size up, 56 px and 17 px type, with a
 * 20 px icon at 20 px from the left and the text from 52 px (`pl-13`).
 */
export const SEARCH_INPUT =
  'h-14 min-w-0 rounded-full border border-line-strong bg-surface px-3 text-[17px] text-ink transition-colors placeholder:text-ink-muted hover:border-ink-faint'

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
        aria-invalid={warning ? true : undefined}
        className={`${INPUT} w-full ${warning ? 'border-status-review' : ''} ${inputClassName ?? ''}`}
        {...input}
      />
      {message && (
        <FieldMessage id={hintId} warning={warning !== undefined}>
          {message}
        </FieldMessage>
      )}
    </div>
  )
}

/**
 * The line under a field: a hint in muted text, or a warning in ink with the review mark. The
 * same line serves inputs that are not a `Field` (a repeated row), so every form's messages
 * look alike and never the browser's bubble.
 */
export function FieldMessage({
  id,
  warning,
  children,
}: {
  id?: string
  warning: boolean
  children: ReactNode
}) {
  return (
    <p
      id={id}
      className={`flex items-center gap-1.5 text-small ${warning ? 'font-medium text-ink' : 'text-ink-muted'}`}
    >
      {warning && <VerdictReview size={14} className="shrink-0" />}
      {children}
    </p>
  )
}
