import { motion } from 'motion/react'
import type { ReactNode } from 'react'
import { Icon } from '../../components/Icon.tsx'
import { rise, stagger } from '../../components/motion.ts'
import { TONES } from '../../components/tones.ts'
import { OrganizationMark, PersonMark } from '@/components/illustrations'
import { useNow } from '../../components/useNow.ts'
import type { Provider, Verification } from '../../lib/api.ts'
import { dateTime, eventTime, plural, utcTitle } from '../../lib/format.ts'
import type { CheckRun } from '../../lib/history.ts'
import { resultLabel } from '../../lib/labels.ts'

/** "What happened": every check run, newest first, then the day the provider was added. */
export function Timeline({
  runs,
  provider,
  sourceName,
}: {
  runs: CheckRun[]
  provider: Provider
  sourceName: (id: string) => string
}) {
  const now = useNow()
  return (
    <section aria-labelledby="timeline-heading">
      <h2 id="timeline-heading" className="text-subheading">
        What happened
      </h2>
      <motion.ol variants={stagger()} initial="hidden" animate="shown" className="mt-5">
        {runs.map((run) => (
          <TimelineStep
            key={run.at}
            at={run.at}
            now={now}
            icon={<Icon name="check" size={13} />}
            title={`Checked ${plural(run.results.length, 'list')}`}
            detail={
              <span className="flex flex-col gap-0.5">
                {run.results.map((result) => (
                  <RunResult key={result.id} result={result} sourceName={sourceName} />
                ))}
              </span>
            }
          />
        ))}
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
    </section>
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
    </span>
  )
}

/** One step of a timeline: its mark on the rule, what happened, and when. */
export function TimelineStep({
  at,
  now,
  icon,
  title,
  detail,
  last = false,
}: {
  at: string
  now: Date
  icon: ReactNode
  title: string
  detail?: ReactNode
  last?: boolean
}) {
  return (
    <motion.li variants={rise} className="relative flex gap-4 pb-7 last:pb-0">
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
