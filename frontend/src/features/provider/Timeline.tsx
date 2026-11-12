import { AnimatePresence, motion } from 'motion/react'
import { useId, useState, type ReactNode } from 'react'
import { Button } from '../../components/Button.tsx'
import { Icon } from '../../components/Icon.tsx'
import { rise, settle, stagger } from '../../components/motion.ts'
import { TONES } from '../../components/tones.ts'
import { OrganizationMark, PersonMark } from '@/components/illustrations'
import { useNow } from '../../components/useNow.ts'
import type { Provider, Verification } from '../../lib/api.ts'
import { dateTime, eventTime, plural, utcTitle } from '../../lib/format.ts'
import { changeLine } from '../../lib/history.ts'
import { resultLabel } from '../../lib/labels.ts'
import { npiStepTitle, retentionLine, type TimelineEntry } from './timeline.ts'

/** The gap between entries an older page adds, as in every list. */
const REVEAL_GAP = 0.035

/** The history's older pages: whether one is left, whether it is loading, and how far back. */
export type OlderHistory = {
  more: boolean
  loading: boolean
  load: () => void
  /** The backend's retention, from the loaded pages (null until the first page answers). */
  retentionDays: number | null
}

/**
 * "What happened": every check run and every NPI verification, newest first, then the day the
 * provider was added. An NPI verification is a step of its own, never counted as a list. The
 * backend sends the history a page at a time; "Show older" adds the next page below, and after
 * the oldest one a line says how long history is kept. The heading's chevron folds the whole
 * list away for this visit (nothing is stored).
 */
export function Timeline({
  entries,
  provider,
  sourceName,
  older,
}: {
  entries: TimelineEntry[]
  provider: Provider
  sourceName: (id: string) => string
  older: OlderHistory
}) {
  const now = useNow()
  const [open, setOpen] = useState(true)
  // Entries from this index on came with "Show older": they rise in one after another.
  const [revealFrom, setRevealFrom] = useState(Number.POSITIVE_INFINITY)
  const panelId = useId()

  function showOlder() {
    if (older.loading) return
    setRevealFrom(entries.length)
    older.load()
  }

  return (
    <section aria-labelledby="timeline-heading">
      {/* The heading is the toggle: the words and the chevron beside them fold the list, so the
          control is where the eye already is (owner, 2026-11-12). */}
      <h2 id="timeline-heading" className="text-subheading">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          aria-controls={panelId}
          className="-ml-2 inline-flex items-center gap-1.5 rounded-full py-1 pr-3 pl-2 text-ink transition-colors hover:bg-soft"
        >
          What happened
          <motion.span
            initial={false}
            animate={{ rotate: open ? 180 : 0 }}
            transition={settle}
            className="inline-flex text-ink-muted"
          >
            <Icon name="chevronDown" size={18} />
          </motion.span>
        </button>
      </h2>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            id={panelId}
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={settle}
            className="overflow-hidden"
          >
            <motion.ol variants={stagger()} initial="hidden" animate="shown" className="pt-5">
              {entries.map((entry, index) => (
                <Entry
                  key={entry.kind === 'npi' ? entry.result.id : entry.at}
                  entry={entry}
                  now={now}
                  sourceName={sourceName}
                  delay={index >= revealFrom ? (index - revealFrom) * REVEAL_GAP : undefined}
                />
              ))}
              {older.more && (
                <motion.li variants={rise} className="relative flex gap-4 pb-7">
                  <span
                    aria-hidden="true"
                    className="absolute top-0 bottom-1 left-3 border-l border-dashed border-line-strong"
                  />
                  <span aria-hidden="true" className="size-6 shrink-0" />
                  <Button
                    variant="ghost"
                    className="-my-1 -ml-4 h-8"
                    aria-busy={older.loading}
                    onClick={showOlder}
                  >
                    {older.loading ? 'Loading…' : 'Show older'}
                  </Button>
                </motion.li>
              )}
              <TimelineStep
                at={provider.created_at}
                now={now}
                icon={
                  provider.entity_kind === 'organization' ? (
                    <OrganizationMark size={13} />
                  ) : (
                    <PersonMark size={13} />
                  )
                }
                title="Added to roster"
                last
              />
            </motion.ol>
            {!older.more && older.retentionDays !== null && (
              <p className="pt-5 text-small text-ink-muted">{retentionLine(older.retentionDays)}</p>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  )
}

/** One entry: a check run with each list's answer, or an NPI step. */
function Entry({
  entry,
  now,
  sourceName,
  delay,
}: {
  entry: TimelineEntry
  now: Date
  sourceName: (id: string) => string
  delay?: number
}) {
  return entry.kind === 'npi' ? (
    <TimelineStep
      at={entry.at}
      now={now}
      delay={delay}
      icon={<Icon name="registry" size={13} />}
      title={npiStepTitle(entry.result)}
      detail={
        <span className="flex flex-col gap-0.5">
          {entry.result.details.npi_set_by === 'user' && <span>NPI set by the user</span>}
          <span>{entry.result.summary}</span>
          <ChangeLine result={entry.result} />
        </span>
      }
    />
  ) : (
    <TimelineStep
      at={entry.at}
      now={now}
      delay={delay}
      icon={<Icon name="check" size={13} />}
      title={`Checked ${plural(entry.results.length, 'list')}`}
      detail={
        <span className="flex flex-col gap-0.5">
          {entry.results.map((result) => (
            <RunResult key={result.id} result={result} sourceName={sourceName} />
          ))}
        </span>
      }
    />
  )
}

/** One source's answer in a run: its own symbol and word, as the backend gave them. */
function RunResult({
  result,
  sourceName,
}: {
  result: Verification
  sourceName: (id: string) => string
}) {
  const label = resultLabel(result)
  const tone = TONES[label.tone]
  return (
    <span>
      <tone.Mark size={14} className={`mr-1.5 inline-block align-[-2px] ${tone.text}`} />
      {sourceName(result.source_id)}: <span className="font-medium text-ink">{label.word}</span>
      <ChangeLine result={result} className="ml-5" />
    </span>
  )
}

/** "changed from No record" under a result the backend marked as changed, with its mark. */
function ChangeLine({ result, className }: { result: Verification; className?: string }) {
  const line = changeLine(result)
  if (!line) return null
  const { Mark, text } = TONES.changed
  return (
    <span className={`flex items-center gap-1 text-caption text-ink-muted ${className ?? ''}`}>
      <Mark size={12} className={`shrink-0 ${text}`} />
      {line}
    </span>
  )
}

/**
 * One step of a timeline: its mark on the rule, what happened, and when. A step added after the
 * list arrived ("Show older") rises in on its own, `delay` seconds late.
 */
export function TimelineStep({
  at,
  now,
  icon,
  title,
  detail,
  last = false,
  delay,
}: {
  at: string
  now: Date
  icon: ReactNode
  title: string
  detail?: ReactNode
  last?: boolean
  delay?: number
}) {
  const motionProps =
    delay === undefined
      ? { variants: rise }
      : {
          initial: { opacity: 0, y: 10 },
          animate: { opacity: 1, y: 0 },
          transition: { ...settle, delay },
        }
  return (
    <motion.li {...motionProps} className="relative flex gap-4 pb-7 last:pb-0">
      {!last && (
        <span aria-hidden="true" className="absolute top-7 bottom-1 left-3 w-px bg-line-strong" />
      )}
      <span className="grid size-6 shrink-0 place-items-center rounded-full border border-line-strong bg-surface text-ink">
        {icon}
      </span>
      <div className="min-w-0">
        <p className="text-body leading-6 font-semibold">{title}</p>
        {detail && <div className="mt-0.5 text-small break-words text-ink-muted">{detail}</div>}
        <time
          dateTime={at}
          title={`${dateTime(at)} · ${utcTitle(at)}`}
          className="mt-0.5 block text-small text-ink-muted"
        >
          {eventTime(at, now)}
        </time>
      </div>
    </motion.li>
  )
}
