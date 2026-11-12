import { AnimatePresence, motion } from 'motion/react'
import type { ReactNode } from 'react'
import { Icon } from '../../components/Icon.tsx'
import { press, quick } from '../../components/motion.ts'
import { TONES } from '../../components/tones.ts'
import type { Label } from '../../lib/labels.ts'
import type { FilterChip, RosterFilter } from '../../lib/monitoring.ts'

/**
 * The roster's filters as one row of outlined pills: "Needs you", each reason with providers
 * (its mark, its word, its count), "All". One row that scrolls sideways on a phone; centred
 * under the search on desktop.
 */
export function FilterChips({
  chips,
  current,
  onChange,
}: {
  chips: FilterChip[]
  current: RosterFilter
  onChange: (filter: RosterFilter) => void
}) {
  return (
    <fieldset className="-mx-5 flex min-w-0 gap-2 overflow-x-auto px-5 py-1 [scrollbar-width:none] lg:mx-0 lg:flex-wrap lg:justify-center lg:overflow-visible lg:px-0">
      <legend className="sr-only">Show</legend>
      {chips.map((chip) => (
        <Chip
          key={chip.filter}
          selected={chip.filter === current}
          label={chip.label}
          count={chip.count}
          onClick={() => onChange(chip.filter)}
        >
          {chip.word}
        </Chip>
      ))}
    </fieldset>
  )
}

/**
 * A filter pill: outlined, its status mark when it names one, its count in muted figures. The
 * pill in use has an ink edge and a blue tick in place of its mark.
 */
export function Chip({
  selected,
  label = null,
  count,
  onClick,
  children,
}: {
  selected: boolean
  label?: Label | null
  count?: number
  onClick: () => void
  children: ReactNode
}) {
  const tone = label ? TONES[label.tone] : null
  return (
    <motion.button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      whileTap={press}
      transition={quick}
      className={`inline-flex h-9 shrink-0 items-center gap-1.5 rounded-full border bg-surface px-3.5 text-small font-medium whitespace-nowrap text-ink transition-colors ${selected ? 'border-ink' : 'border-line-strong hover:border-ink-faint'}`}
    >
      <AnimatePresence mode="popLayout" initial={false}>
        {selected ? (
          <motion.span
            key="tick"
            initial={{ opacity: 0, scale: 0.6 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.6 }}
            transition={quick}
            className="grid size-4 place-items-center rounded-full bg-accent text-on-ink"
          >
            <Icon name="check" size={11} />
          </motion.span>
        ) : (
          tone && (
            <motion.span
              key="mark"
              initial={{ opacity: 0, scale: 0.6 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.6 }}
              transition={quick}
              className="inline-flex"
            >
              <tone.Mark size={14} className={tone.text} />
            </motion.span>
          )
        )}
      </AnimatePresence>
      {children}
      {count !== undefined && (
        <span className="text-ink-muted tabular-nums">{new Intl.NumberFormat().format(count)}</span>
      )}
    </motion.button>
  )
}
