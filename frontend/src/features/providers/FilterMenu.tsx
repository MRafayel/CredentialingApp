import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useId, useRef, useState } from 'react'
import { Icon } from '../../components/Icon.tsx'
import { press, quick } from '../../components/motion.ts'
import type { RosterOrder } from '../../lib/api.ts'
import {
  KIND_CHOICES,
  ORDER_CHOICES,
  choicesInUse,
  toggled,
  type Choice,
  type RosterKind,
} from '../../lib/monitoring.ts'

/**
 * The roster's other filters, beside the search and over whichever chip is in use: who to show
 * (individuals, organizations) and the order (newest, oldest; needs you first is the default
 * and has no line). A round glass button with the sliders icon and the count of choices in use;
 * it opens one card, a tick on each choice in use, one choice per line. A choice pressed again
 * clears it (owner, 2026-11-12). Closes on a choice, a click outside or Escape.
 */
export function FilterMenu({
  kind,
  order,
  onKind,
  onOrder,
}: {
  kind: RosterKind
  order: RosterOrder
  onKind: (kind: RosterKind) => void
  onOrder: (order: RosterOrder) => void
}) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const id = useId()
  const inUse = choicesInUse(kind, order)

  useEffect(() => {
    if (!open) return
    function onPointerDown(event: PointerEvent) {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false)
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  return (
    <div ref={root} className="relative w-fit shrink-0">
      <motion.button
        type="button"
        aria-label={inUse ? `Filter, ${inUse} in use` : 'Filter'}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((was) => !was)}
        whileTap={press}
        transition={quick}
        className={`glass-bar relative grid size-14 place-items-center rounded-full text-ink transition-[color,box-shadow] ${open ? 'ring-1 ring-ink' : ''}`}
      >
        <Icon name="sliders" size={24} />
        <AnimatePresence>
          {inUse > 0 && (
            <motion.span
              initial={{ scale: 0.5, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.5, opacity: 0 }}
              transition={quick}
              className="absolute -top-0.5 -right-0.5 grid size-5 place-items-center rounded-full bg-ink text-caption font-medium text-on-ink tabular-nums"
            >
              {inUse}
            </motion.span>
          )}
        </AnimatePresence>
      </motion.button>
      <AnimatePresence>
        {open && (
          <motion.div
            id={id}
            initial={{ opacity: 0, y: -4, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -4, scale: 0.98 }}
            transition={quick}
            // Hung from the button: leftwards on a phone (the button is at the edge), rightwards
            // on desktop (the search is on its left).
            className="absolute top-full right-0 z-20 mt-2 w-56 origin-top-right rounded-card border border-line bg-surface p-2 shadow-card-hover lg:right-auto lg:left-0 lg:origin-top-left"
          >
            <Group
              name="Show"
              choices={KIND_CHOICES}
              current={kind}
              onChoose={(next) => {
                onKind(toggled<RosterKind>(kind, next, 'all'))
                setOpen(false)
              }}
            />
            <div className="mx-2 my-1.5 border-t border-line" />
            <Group
              name="Order"
              choices={ORDER_CHOICES}
              current={order}
              onChoose={(next) => {
                onOrder(toggled<RosterOrder>(order, next, 'attention'))
                setOpen(false)
              }}
            />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

/** One group of the menu: a small heading, one choice per line, a tick on the current one. */
function Group<T extends string>({
  name,
  choices,
  current,
  onChoose,
}: {
  name: string
  choices: readonly Choice<T>[]
  /** The choice in use, or a default no line names. */
  current: string
  onChoose: (choice: T) => void
}) {
  return (
    <fieldset className="min-w-0">
      <legend className="px-3 pt-1.5 pb-1 text-caption font-medium text-ink-muted">{name}</legend>
      {choices.map((choice) => {
        const chosen = choice.value === current
        return (
          <button
            key={choice.value}
            type="button"
            aria-pressed={chosen}
            onClick={() => onChoose(choice.value)}
            className={`flex h-9 w-full items-center gap-2 rounded-lg px-3 text-left text-small transition-colors hover:bg-soft ${chosen ? 'font-medium text-ink' : 'text-ink'}`}
          >
            <span
              className={`grid size-4 shrink-0 place-items-center rounded-full ${chosen ? 'bg-accent text-on-ink' : ''}`}
            >
              {chosen && <Icon name="check" size={11} />}
            </span>
            {choice.word}
          </button>
        )
      })}
    </fieldset>
  )
}
