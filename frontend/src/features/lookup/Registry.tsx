import { AnimatePresence, motion } from 'motion/react'
import { useState } from 'react'
import { Link } from 'react-router'
import { VerdictBlocked } from '@/components/illustrations'
import { Button } from '../../components/Button.tsx'
import { Icon } from '../../components/Icon.tsx'
import { settle, stagger } from '../../components/motion.ts'
import { ErrorNote } from '../../components/Note.tsx'
import { SourceLine } from '../../components/Provenance.tsx'
import { Skeleton } from '../../components/Skeleton.tsx'
import { StatusLabel } from '../../components/Status.tsx'
import { CalendarDate } from '../../components/Time.tsx'
import { plural } from '../../lib/format.ts'
import type { MirrorSource } from '../../lib/api.ts'
import { registryStatusLabel } from '../../lib/labels.ts'
import { useNpi, type useNpiSearch } from '../../lib/queries.ts'
import { registryName } from '../../lib/registry.ts'
import { useFrom } from '../shell/back.ts'
import { CARD, CARD_LINK } from './cards.ts'
import { NpiSearchSheet } from './NpiSearchSheet.tsx'
import { NAME_LIMIT, summaryFacts } from './npiSearch.ts'
import { LiveLine, RegistryRow, RegistryRowSkeleton } from './RegistryParts.tsx'

/** The small spaced-capital caption over a card's name, as a lookup card names its list. */
const CAPTION =
  'truncate text-[11px] leading-4 font-semibold tracking-[0.06em] text-ink-muted uppercase'

/**
 * The registry's answer for a ten-digit lookup, above the exclusion results: the backend's
 * status with where it comes from, the name and place as registered, and the way to the
 * registry page (the whole card).
 */
export function RegistryCard({ npi }: { npi: string }) {
  const lookup = useNpi(npi)
  const from = useFrom()
  if (lookup.isError && !lookup.data) {
    return (
      <ErrorNote
        action={
          <Button variant="ghost" className="-my-1 h-8" onClick={() => void lookup.refetch()}>
            Try again
          </Button>
        }
      >
        {lookup.error.message}
      </ErrorNote>
    )
  }
  if (!lookup.data) return <RegistryCardSkeleton />
  const { status, record, mirror, live } = lookup.data
  const name = record && registryName(record)
  const facts = record ? summaryFacts(record) : []
  return (
    <motion.article
      aria-label="NPI registry"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={settle}
      className={`group ${CARD}`}
    >
      <div className="flex flex-col gap-4 p-5 lg:grid lg:grid-cols-[minmax(0,1.45fr)_minmax(0,2fr)_auto] lg:items-center lg:gap-8 lg:px-7 lg:py-6">
        <div className="min-w-0">
          <p className={CAPTION}>NPI registry</p>
          <h3 className="mt-1.5 text-[17px] leading-snug font-semibold break-words text-ink">
            <Link to={`/npi/${npi}`} state={from} className={CARD_LINK}>
              {name ?? (record ? 'Name not published' : `NPI ${npi}`)}
            </Link>
          </h3>
          <p className="mt-0.5 text-small break-words text-ink-muted">
            {[...(record ? [`NPI ${npi}`] : []), ...facts].join(' · ') || 'No registry row'}
          </p>
        </div>
        <div className="flex min-w-0 flex-col gap-1">
          <StatusLabel label={registryStatusLabel(status)} size={15} chip className="text-body" />
          <div className="flex flex-col gap-0.5 pl-9">
            {record?.deactivation_date && (
              <span className="text-small text-ink">
                Since <CalendarDate value={record.deactivation_date} />
              </span>
            )}
            {mirror && <SourceLine block={mirror} kind="mirror" />}
            {live && <LiveLine live={live} />}
          </div>
        </div>
        <Icon
          name="chevronRight"
          size={16}
          className="hidden shrink-0 text-ink-faint transition-[transform,color] group-hover:translate-x-0.5 group-hover:text-ink lg:block"
        />
      </div>
    </motion.article>
  )
}

function RegistryCardSkeleton() {
  return (
    <div aria-label="Asking the NPI registry" className="rounded-item bg-surface shadow-card">
      <div className="flex flex-col gap-4 p-5 lg:grid lg:grid-cols-[minmax(0,1.45fr)_minmax(0,2fr)_auto] lg:items-center lg:gap-8 lg:px-7 lg:py-6">
        <div className="flex flex-col gap-2">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-4 w-56 max-w-full" />
        </div>
        <div className="flex items-center gap-2">
          <Skeleton className="size-7 rounded-full" />
          <Skeleton className="h-3.5 w-48" />
        </div>
        <span className="hidden w-4 lg:block" />
      </div>
    </div>
  )
}

/** "3 NPIs", "First 10 NPIs" for a full page, "No exact match". */
function namesCount(count: number): string {
  if (count === 0) return 'No exact match'
  return count >= NAME_LIMIT ? `First ${count} NPIs` : plural(count, 'NPI')
}

/** The mirror search a name lookup runs (`useNpiSearch` with `NAME_LIMIT`), owned by the page. */
export type NameSearch = ReturnType<typeof useNpiSearch>

/**
 * Where the mirror's answer comes from ("Cached · NPPES monthly file · as of Nov 2, 2026"), or
 * that the mirror is not loaded, so an empty answer is never read as a searched registry.
 */
export function MirrorLine({
  mirror,
  className,
}: {
  mirror: MirrorSource | null
  className?: string
}) {
  return mirror ? (
    <SourceLine block={mirror} kind="mirror" className={className} />
  ) : (
    <p
      className={`inline-flex items-center gap-1.5 text-small font-semibold text-ink ${className ?? ''}`}
    >
      <VerdictBlocked size={13} className="shrink-0" />
      The registry mirror is not loaded
    </p>
  )
}

/** "Search NPPES live" and the sheet it opens, prefilled from the lookup's query. */
export function LiveSearchButton({ query, className }: { query: string; className?: string }) {
  const [asking, setAsking] = useState(false)
  return (
    <>
      <Button variant="secondary" icon="bolt" className={className} onClick={() => setAsking(true)}>
        Search NPPES live
      </Button>
      <AnimatePresence>
        {asking && <NpiSearchSheet query={query} onClose={() => setAsking(false)} />}
      </AnimatePresence>
    </>
  )
}

/**
 * Under the lists' matches when the mirror has no NPI with this name: one muted line (the
 * fact and where it comes from) over the way to ask the live registry, centred. Not a section.
 */
export function RegistryNote({ query, mirror }: { query: string; mirror: MirrorSource | null }) {
  return (
    <div className="mt-8 flex flex-col items-center gap-4 text-center lg:mt-10">
      <p className="flex flex-wrap items-center justify-center gap-x-1.5 text-small text-ink-muted">
        <span>No NPI with this name in the registry</span>
        {/* The dot wraps with the source line, never left dangling at a line's end. */}
        <span className="inline-flex flex-wrap items-center gap-x-1.5">
          <span aria-hidden="true">·</span>
          <MirrorLine mirror={mirror} />
        </span>
      </p>
      <LiveSearchButton query={query} />
    </div>
  )
}

/**
 * A name lookup's answer from the registry, under the exclusion results: the mirror's NPIs with
 * exactly this name, as of the mirror's load, and the way to ask the live registry instead.
 */
export function RegistryNames({
  q,
  search,
  alone = false,
}: {
  q: string
  search: NameSearch
  /** Whether the section is the whole answer (the lists found nothing): no room above it. */
  alone?: boolean
}) {
  const data = search.data
  return (
    <section aria-labelledby="registry-heading" className={alone ? '' : 'mt-12 lg:mt-16'}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 id="registry-heading" className="text-subheading">
          NPI Registry
        </h2>
        {data && (
          <span className="text-body text-ink-muted tabular-nums">
            {namesCount(data.records.length)}
          </span>
        )}
      </div>
      {data && <MirrorLine mirror={data.mirror} className="mt-1" />}
      <div className="mt-4">
        {search.isError && !data ? (
          <ErrorNote
            action={
              <Button variant="ghost" className="-my-1 h-8" onClick={() => void search.refetch()}>
                Try again
              </Button>
            }
          >
            {search.error.message}
          </ErrorNote>
        ) : !data ? (
          <ul aria-label="Searching the NPI registry" className="flex flex-col gap-3">
            <RegistryRowSkeleton />
          </ul>
        ) : (
          data.records.length > 0 && (
            <motion.ul
              key={q}
              variants={stagger()}
              initial="hidden"
              animate="shown"
              style={{ opacity: search.isPlaceholderData ? 0.6 : 1 }}
              className="flex flex-col gap-3 transition-opacity duration-200"
            >
              {data.records.map((record) => (
                <RegistryRow key={record.npi} record={record} />
              ))}
            </motion.ul>
          )
        )}
      </div>
      <LiveSearchButton query={q} className="mt-4" />
    </section>
  )
}
