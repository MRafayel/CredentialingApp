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
import { registryStatusLabel } from '../../lib/labels.ts'
import { useNpi, useNpiSearch } from '../../lib/queries.ts'
import { registryName } from '../../lib/registry.ts'
import { CARD, CARD_LINK } from './cards.ts'
import { NpiSearchSheet } from './NpiSearchSheet.tsx'
import { summaryFacts } from './npiSearch.ts'
import { LiveLine, RegistryRow, RegistryRowSkeleton } from './RegistryParts.tsx'

/** How many registry rows a name lookup shows from the mirror. */
const NAME_LIMIT = 10

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
            <Link to={`/npi/${npi}`} className={CARD_LINK}>
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

/**
 * A name lookup's answer from the registry, under the exclusion results: the mirror's NPIs with
 * exactly this name, as of the mirror's load, and the way to ask the live registry instead.
 */
export function RegistryNames({ q }: { q: string }) {
  const search = useNpiSearch({ q, limit: NAME_LIMIT }, true)
  const [asking, setAsking] = useState(false)
  const data = search.data
  return (
    <section aria-labelledby="registry-heading" className="mt-12 lg:mt-16">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 id="registry-heading" className="text-subheading">
          In the NPI registry
        </h2>
        {data && (
          <span className="text-body text-ink-muted tabular-nums">
            {namesCount(data.records.length)}
          </span>
        )}
      </div>
      {data &&
        (data.mirror ? (
          <SourceLine block={data.mirror} kind="mirror" className="mt-1" />
        ) : (
          <p className="mt-1 inline-flex items-center gap-1.5 text-small font-semibold text-ink">
            <VerdictBlocked size={13} className="shrink-0" />
            The registry mirror is not loaded
          </p>
        ))}
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
      <Button variant="secondary" icon="bolt" className="mt-4" onClick={() => setAsking(true)}>
        Search NPPES live
      </Button>
      <AnimatePresence>
        {asking && <NpiSearchSheet query={q} onClose={() => setAsking(false)} />}
      </AnimatePresence>
    </section>
  )
}
