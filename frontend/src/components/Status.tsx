import { AnimatePresence, motion } from 'motion/react'
import type { Label } from '../lib/labels.ts'
import { quick } from './motion.ts'
import { TONES } from './tones.ts'

/**
 * A status as a coloured symbol and an ink word ("● No record"); a change cross-fades between
 * the two states. Statuses are never pills: pills are for actions and codes. `chip` sets the
 * mark on a 28 px soft circle in its tint, so the status reads from a distance in a list.
 */
export function StatusLabel({
  label,
  size = 16,
  chip = false,
  className,
}: {
  label: Label
  size?: number
  chip?: boolean
  className?: string
}) {
  const tone = TONES[label.tone]
  return (
    <span className={`inline-flex shrink-0 items-center font-medium text-ink ${className ?? ''}`}>
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.span
          key={`${label.tone}-${label.word}`}
          className={`inline-flex items-center whitespace-nowrap ${chip ? 'gap-2' : 'gap-1.5'}`}
          initial={{ opacity: 0, y: 3 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -3 }}
          transition={quick}
        >
          {chip ? (
            <span
              className={`grid size-7 shrink-0 place-items-center rounded-full ${tone.tint} ${tone.text}`}
            >
              <tone.Mark size={size} />
            </span>
          ) : (
            <tone.Mark size={size} className={`shrink-0 ${tone.text}`} />
          )}
          {label.word}
        </motion.span>
      </AnimatePresence>
    </span>
  )
}

/** A short code in an outlined pill: a state, a jurisdiction. */
export function CodePill({ children }: { children: string }) {
  return (
    <span className="inline-flex h-6 items-center rounded-full border border-line-strong px-2 text-caption font-semibold text-ink-muted">
      {children}
    </span>
  )
}

/**
 * Freshness as quiet text with a clock whose colour tells them apart: Live (blue), Cached and
 * Manually verified (grey), Stale (amber). The word always says which.
 */
export function Freshness({ label, className }: { label: Label; className?: string }) {
  const tone = TONES[label.tone]
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap ${className ?? ''}`}>
      <svg
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        aria-hidden="true"
        className={tone.text}
      >
        <circle cx="12" cy="12" r="8.5" stroke="currentColor" strokeWidth="2.2" />
        <path d="M12 7.5V12l3 2" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
      </svg>
      {label.word}
    </span>
  )
}
