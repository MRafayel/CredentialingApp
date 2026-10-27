/**
 * The typed text and a blinking caret, laid over the input where its placeholder sits. Hidden
 * from assistive technology: the input keeps its real `placeholder` and label. The parent
 * positions it (`className`) to match the input's padding and type size.
 */
export function TypedPlaceholder({ text, className }: { text: string; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={`pointer-events-none absolute top-1/2 flex -translate-y-1/2 items-center overflow-hidden whitespace-pre text-ink-muted ${className ?? ''}`}
    >
      {text}
      <span className="ml-px inline-block h-[1.15em] w-[1.5px] shrink-0 rounded-full bg-ink-muted caret-blink" />
    </span>
  )
}
