import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useState } from 'react'
import { Icon } from './Icon.tsx'
import { press, quick } from './motion.ts'

/** An identifier in monospace that copies on click and says so briefly. */
export function CopyValue({
  value,
  label,
  className,
}: {
  value: string
  label: string
  className?: string
}) {
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!copied) return
    const timer = setTimeout(() => setCopied(false), 1600)
    return () => clearTimeout(timer)
  }, [copied])

  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }

  return (
    <motion.button
      type="button"
      onClick={(event) => {
        event.preventDefault()
        event.stopPropagation()
        void copy()
      }}
      whileTap={press}
      transition={quick}
      aria-label={`Copy ${label} ${value}`}
      className={`group/copy inline-flex h-7 items-center gap-1.5 rounded-full px-1.5 font-mono text-small tabular-nums transition-colors hover:text-ink ${copied ? 'text-accent-strong' : 'text-ink-muted'} ${className ?? ''}`}
    >
      {value}
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.span
          key={copied ? 'copied' : 'copy'}
          initial={{ opacity: 0, scale: 0.8 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, scale: 0.8 }}
          transition={quick}
          className="inline-flex items-center gap-1 font-sans text-caption font-medium"
        >
          {copied ? (
            <>
              <Icon name="check" size={14} />
              Copied
            </>
          ) : (
            <Icon
              name="copy"
              size={14}
              className="text-ink-faint transition-colors group-hover/copy:text-ink"
            />
          )}
        </motion.span>
      </AnimatePresence>
      <span className="sr-only" aria-live="polite">
        {copied ? `${label} copied` : ''}
      </span>
    </motion.button>
  )
}
