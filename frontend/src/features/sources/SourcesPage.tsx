import { motion } from 'motion/react'
import type { ReactNode } from 'react'
import { EmptySources, VerdictListed, VerdictReview } from '@/components/illustrations'
import { Button } from '../../components/Button.tsx'
import { Icon } from '../../components/Icon.tsx'
import { rise, stagger } from '../../components/motion.ts'
import { ErrorNote } from '../../components/Note.tsx'
import { PageBar, PageBody, PageHeader } from '../../components/PageBar.tsx'
import { Skeleton } from '../../components/Skeleton.tsx'
import { CodePill, Freshness, StatusLabel } from '../../components/Status.tsx'
import { Ago, DateOf, Until } from '../../components/Time.tsx'
import type { SourceStatus } from '../../lib/api.ts'
import { freshnessLabel } from '../../lib/labels.ts'
import { useSources } from '../../lib/queries.ts'
import { healthLabel, sourcesSummary } from '../../lib/sources.ts'

const NONE = <span className="text-ink-muted">—</span>

/** The columns of `make sources`, in the same order. */
const COLUMNS = [
  'Source',
  'Jur.',
  'Last fetch',
  'Result',
  'Records',
  'List last updated',
  'Fresh',
  'Next due',
] as const

export function SourcesPage() {
  const sources = useSources()
  return (
    <>
      <PageBar title="Sources" />
      <PageBody>
        <PageHeader
          title="Sources"
          subtitle={
            sources.data ? (
              <span className="tabular-nums">{sourcesSummary(sources.data)}</span>
            ) : (
              <span className="skeleton inline-block h-4 w-56 rounded-md align-middle" />
            )
          }
        />
        <div className="mt-8 lg:mt-12">
          {sources.isError && !sources.data ? (
            <ErrorNote
              action={
                <Button
                  variant="ghost"
                  className="-my-1 h-8"
                  onClick={() => void sources.refetch()}
                >
                  Try again
                </Button>
              }
            >
              {sources.error.message}
            </ErrorNote>
          ) : sources.isPending ? (
            <div className="flex flex-col gap-px overflow-hidden rounded-card bg-surface shadow-card">
              <Skeleton className="h-14 rounded-none" />
              <Skeleton className="h-16 rounded-none" />
              <Skeleton className="h-16 rounded-none" />
            </div>
          ) : sources.data.length === 0 ? (
            <div className="mx-auto mt-8 flex max-w-sm flex-col items-center gap-4 text-center">
              <EmptySources size={116} />
              <h2 className="text-subheading font-semibold">No sources registered</h2>
            </div>
          ) : (
            <>
              <SourcesTable sources={sources.data} />
              <motion.ul
                variants={stagger()}
                initial="hidden"
                animate="shown"
                className="divide-y divide-line overflow-hidden rounded-card bg-surface shadow-card lg:hidden"
              >
                {sources.data.map((source) => (
                  <motion.li key={source.source_id} variants={rise}>
                    <SourceRow source={source} />
                  </motion.li>
                ))}
              </motion.ul>
            </>
          )}
        </div>
      </PageBody>
    </>
  )
}

/**
 * Desktop: one table on a card, a row per source (tinted on hover) and a note row under a
 * failed fetch.
 */
function SourcesTable({ sources }: { sources: readonly SourceStatus[] }) {
  return (
    <div className="hidden overflow-hidden rounded-card bg-surface shadow-card lg:block">
      <table className="w-full text-left text-small">
        <thead>
          <tr className="border-b border-line">
            {COLUMNS.map((column) => (
              <th
                key={column}
                scope="col"
                className={`px-4 py-3 font-medium whitespace-nowrap text-ink-muted first:pl-6 last:pr-6 ${column === 'Records' ? 'text-right' : ''}`}
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <motion.tbody variants={stagger()} initial="hidden" animate="shown">
          {sources.map((source) => (
            <SourceTableRows key={source.source_id} source={source} />
          ))}
        </motion.tbody>
      </table>
    </div>
  )
}

function SourceTableRows({ source }: { source: SourceStatus }) {
  const loaded = source.latest_completed !== null
  const pad = 'px-4 align-top first:pl-6 last:pr-6'
  const cell = `${pad} py-4`
  return (
    <>
      <motion.tr
        variants={rise}
        className={`border-line transition-colors duration-150 hover:bg-soft/60 ${source.problem ? '' : 'border-b last:border-b-0'}`}
      >
        <th scope="row" className={`${cell} max-w-[280px] font-normal`}>
          <span className="block text-body font-semibold text-ink">{source.display_name}</span>
          <OfficialLink href={source.source_url} />
        </th>
        <td className={cell}>
          <span className="flex flex-wrap gap-1">
            {source.jurisdictions.map((jurisdiction) => (
              <CodePill key={jurisdiction}>{jurisdiction}</CodePill>
            ))}
          </span>
        </td>
        <td className={`${cell} whitespace-nowrap`}>
          {source.latest ? <Ago at={source.latest.fetched_at} /> : 'never'}
        </td>
        {/* The tinted circle is 28 px tall: a little less padding keeps its word on the row's line. */}
        <td className={`${pad} py-3`}>
          <StatusLabel label={healthLabel(source.health)} size={14} chip />
        </td>
        <td className={`${cell} text-right font-semibold tabular-nums`}>
          {loaded ? new Intl.NumberFormat().format(source.current_records) : NONE}
        </td>
        <td className={`${cell} whitespace-nowrap tabular-nums`}>
          {source.data_as_of ? <DateOf at={source.data_as_of} /> : NONE}
        </td>
        <td className={`${cell} text-ink-muted`}>
          {source.freshness ? <Freshness label={freshnessLabel(source.freshness)} /> : NONE}
        </td>
        <td className={`${cell} whitespace-nowrap`}>
          <NextDue source={source} />
        </td>
      </motion.tr>
      {source.problem && (
        <motion.tr variants={rise} className="border-b border-line last:border-b-0">
          <td colSpan={COLUMNS.length} className="px-6 pt-0 pb-4">
            <Problem source={source} />
          </td>
        </motion.tr>
      )}
    </>
  )
}

/** Phone: the same facts stacked, as a hairline row per source. */
function SourceRow({ source }: { source: SourceStatus }) {
  const loaded = source.latest_completed !== null
  return (
    <article className="flex flex-col gap-3 px-5 py-4">
      <header className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-title">{source.display_name}</h2>
          <div className="mt-1.5 flex flex-wrap gap-1">
            {source.jurisdictions.map((jurisdiction) => (
              <CodePill key={jurisdiction}>{jurisdiction}</CodePill>
            ))}
          </div>
        </div>
        <StatusLabel label={healthLabel(source.health)} size={14} chip className="text-small" />
      </header>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-small">
        <Fact term="Records">
          <span className="text-title tabular-nums">
            {loaded ? new Intl.NumberFormat().format(source.current_records) : '—'}
          </span>
        </Fact>
        <Fact term="Fresh">
          {source.freshness ? <Freshness label={freshnessLabel(source.freshness)} /> : '—'}
        </Fact>
        <Fact term="List last updated">
          {source.data_as_of ? <DateOf at={source.data_as_of} /> : '—'}
        </Fact>
        <Fact term="Next due">
          <NextDue source={source} />
        </Fact>
      </dl>
      {source.problem && <Problem source={source} />}
      <footer className="flex items-center gap-3 text-small text-ink-muted">
        <span>Last fetch {source.latest ? <Ago at={source.latest.fetched_at} /> : 'never'}</span>
        <span className="ml-auto">
          <OfficialLink href={source.source_url} />
        </span>
      </footer>
    </article>
  )
}

function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-ink-muted">{term}</dt>
      <dd className="mt-0.5 font-medium">{children}</dd>
    </div>
  )
}

function NextDue({ source }: { source: SourceStatus }) {
  if (!source.next_due) return NONE
  return (
    <span className="inline-flex items-center gap-1.5">
      {source.health === 'overdue' && <VerdictReview size={14} className="shrink-0" />}
      <Until at={source.next_due} />
    </span>
  )
}

function Problem({ source }: { source: SourceStatus }) {
  return (
    <p className="flex items-start gap-2 text-small break-words text-ink">
      <VerdictListed size={14} className="mt-0.5 shrink-0" />
      <span>
        <span className="font-semibold">
          {source.latest?.outcome.replaceAll('_', ' ') ?? 'Problem'}:
        </span>{' '}
        {source.problem}
      </span>
    </p>
  )
}

function OfficialLink({ href }: { href: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="mt-0.5 inline-flex items-center gap-1 rounded-full text-small font-medium text-accent-strong underline-offset-4 hover:underline"
    >
      Official page
      <Icon name="external" size={13} />
      <span className="sr-only">(opens in a new tab)</span>
    </a>
  )
}
