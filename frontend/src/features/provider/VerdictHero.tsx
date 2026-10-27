import { AnimatePresence, motion, useReducedMotion, useScroll, useTransform } from 'motion/react'
import type { ReactNode } from 'react'
import { Button } from '../../components/Button.tsx'
import { settle } from '../../components/motion.ts'
import { TONES } from '../../components/tones.ts'
import { Ago, DateOf } from '../../components/Time.tsx'
import type { ProviderVerdict } from '../../lib/api.ts'
import { verdictLabel } from '../../lib/labels.ts'

/** How far the wash lags behind the page over the first screen of scrolling, in px. */
const WASH_DRIFT = 6

/**
 * The answer first: the backend's verdict as a large mark on a soft circle in its tint and the
 * word in ink, the figures it gives, and the one action. The card sits on --shadow-card over a
 * faint wash in the verdict's colour, which drifts a few pixels slower than the page scrolls.
 */
export function VerdictHero({
  verdict,
  expected,
  running,
  onRun,
}: {
  verdict: ProviderVerdict
  expected: number
  running: boolean
  onRun: () => void
}) {
  const label = verdictLabel(verdict)
  const tone = TONES[label.tone]
  const checked = verdict.verdict !== 'not_checked'
  const reduced = useReducedMotion() ?? false
  const { scrollY } = useScroll()
  const drift = useTransform(scrollY, [0, 480], [0, WASH_DRIFT], { clamp: true })

  return (
    <div className="relative isolate">
      <motion.div
        aria-hidden="true"
        style={{ y: reduced ? 0 : drift }}
        className="pointer-events-none absolute -inset-x-16 -inset-y-20 -z-10"
      >
        <AnimatePresence initial={false}>
          <motion.div
            key={label.tone}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.5 }}
            className={`absolute inset-0 bg-wash-tone ${tone.wash}`}
          />
        </AnimatePresence>
      </motion.div>
      <section
        aria-label="Verdict"
        className="rounded-hero bg-surface p-6 shadow-card sm:p-8 lg:p-10"
      >
        <div className="flex flex-col gap-6 sm:flex-row sm:items-center">
          <div className="flex min-w-0 flex-1 items-center gap-5">
            <AnimatePresence mode="popLayout" initial={false}>
              <motion.span
                key={verdict.verdict}
                initial={{ opacity: 0, scale: 0.85 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.85 }}
                transition={settle}
                className={`grid size-20 shrink-0 place-items-center rounded-full sm:size-24 ${tone.tint} ${tone.text}`}
              >
                <tone.Mark size={52} className="max-sm:size-11" />
              </motion.span>
            </AnimatePresence>
            <div className="min-w-0" aria-live="polite">
              <AnimatePresence mode="popLayout" initial={false}>
                <motion.div
                  key={`${label.word}|${label.detail ?? ''}`}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -8 }}
                  transition={settle}
                >
                  <p className="text-display">{label.word}</p>
                  <p className="mt-1 text-body text-ink-muted">
                    {label.detail ?? (checked ? '' : 'No check has run yet')}
                  </p>
                </motion.div>
              </AnimatePresence>
            </div>
          </div>
          <Button
            variant="primary"
            icon={checked ? 'refresh' : 'check'}
            onClick={onRun}
            disabled={running}
            aria-busy={running}
            className="h-11 w-full sm:w-auto sm:self-center"
          >
            {running ? 'Checking…' : checked ? 'Check again' : 'Run check'}
          </Button>
        </div>
        <dl className="mt-6 grid divide-y divide-line border-t border-line sm:mt-8 sm:grid-cols-3 sm:divide-x sm:divide-y-0 sm:pt-6 lg:mt-10">
          <Figure term="Sources" first>
            <span className="tabular-nums">{checked ? verdict.sources_checked : expected}</span>
          </Figure>
          <Figure term="List last updated">
            {verdict.oldest_list_updated_at ? <DateOf at={verdict.oldest_list_updated_at} /> : '—'}
          </Figure>
          <Figure term="Last checked">
            {verdict.last_checked_at ? <Ago at={verdict.last_checked_at} /> : 'Never'}
          </Figure>
        </dl>
      </section>
    </div>
  )
}

function Figure({
  term,
  first = false,
  children,
}: {
  term: string
  first?: boolean
  children: ReactNode
}) {
  return (
    <div
      className={`flex min-w-0 items-baseline justify-between gap-4 py-3 sm:block sm:py-0 ${first ? 'sm:pr-6' : 'sm:px-6'}`}
    >
      <dt className="text-small text-ink-muted">{term}</dt>
      <dd className="truncate text-[17px] font-semibold tracking-tight tabular-nums sm:mt-1 sm:text-figure">
        {children}
      </dd>
    </div>
  )
}
