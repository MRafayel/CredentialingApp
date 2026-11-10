import { motion } from 'motion/react'
import { Link } from 'react-router'
import { VerdictBlocked } from '@/components/illustrations'
import { CopyValue } from '../../components/CopyValue.tsx'
import { Icon } from '../../components/Icon.tsx'
import { rise } from '../../components/motion.ts'
import { SourceLine } from '../../components/Provenance.tsx'
import { Skeleton } from '../../components/Skeleton.tsx'
import { StatusLabel } from '../../components/Status.tsx'
import { Ago } from '../../components/Time.tsx'
import type { LiveCheck, NpiSummary } from '../../lib/api.ts'
import { npiStatusLabel, outcomeWord } from '../../lib/labels.ts'
import { registryName, registrySourceName } from '../../lib/registry.ts'
import { CARD, CARD_LINK } from './cards.ts'
import { summaryFacts } from './npiSearch.ts'

/* The registry's small pieces, shared by the lookup, the live search and the registry page. */

export { RegistryLink } from '../../components/RegistryLink.tsx'

/**
 * Where a live registry answer comes from. A request the registry answered reads as the shared
 * `SourceLine` ("Live · NPPES live registry · 5 s ago"); one it did not answer names its outcome
 * in ink with the blocked mark, never as "not found".
 */
export function LiveLine({ live, className }: { live: LiveCheck; className?: string }) {
  if (live.outcome === 'completed') {
    return <SourceLine block={live} kind="live" className={className} />
  }
  return (
    <span
      className={`inline-flex flex-wrap items-center gap-x-1.5 text-small text-ink-muted ${className ?? ''}`}
    >
      <span className="inline-flex items-center gap-1 font-semibold text-ink">
        <VerdictBlocked size={13} className="shrink-0" />
        {outcomeWord(live.outcome)}
      </span>
      <span aria-hidden="true">·</span>
      <span>{registrySourceName(live.provenance.source_id)}</span>
      <span aria-hidden="true">·</span>
      <Ago at={live.provenance.verified_at} />
    </span>
  )
}

/**
 * One NPI of a registry search as a compact card. It reads across once the card itself is wide
 * enough (a container query), so it stacks inside the search sheet as on a phone. that opens its registry page: the name as
 * registered, what the row says about the party, the NPI and the row's own status.
 */
export function RegistryRow({ record }: { record: NpiSummary }) {
  const name = registryName(record)
  const facts = summaryFacts(record)
  return (
    <motion.li variants={rise} className={`group @container ${CARD}`}>
      <div className="flex flex-col gap-2 px-5 py-4 @xl:flex-row @xl:items-center @xl:gap-6">
        <div className="min-w-0 flex-1">
          <h3 className="text-title break-words text-ink">
            <Link to={`/npi/${record.npi}`} className={CARD_LINK}>
              {name ?? 'Name not published'}
            </Link>
          </h3>
          {facts.length > 0 && (
            <p className="mt-0.5 text-small break-words text-ink-muted">{facts.join(' · ')}</p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 @xl:flex-nowrap @xl:gap-6">
          <StatusLabel
            label={npiStatusLabel(record.status)}
            size={14}
            chip
            className="text-small @xl:w-32"
          />
          <CopyValue value={record.npi} label="NPI" className="relative z-10 -ml-1.5 @xl:ml-0" />
        </div>
        <Icon
          name="chevronRight"
          size={16}
          className="hidden shrink-0 text-ink-faint transition-[transform,color] group-hover:translate-x-0.5 group-hover:text-ink @xl:block"
        />
      </div>
    </motion.li>
  )
}

export function RegistryRowSkeleton() {
  return (
    <li className="@container rounded-item bg-surface shadow-card">
      <div className="flex flex-col gap-2 px-5 py-4 @xl:flex-row @xl:items-center @xl:gap-6">
        <div className="flex flex-1 flex-col gap-2">
          <Skeleton className="h-4 w-48 max-w-full" />
          <Skeleton className="h-3 w-32" />
        </div>
        <div className="flex items-center gap-2 @xl:w-32">
          <Skeleton className="size-7 rounded-full" />
          <Skeleton className="h-3.5 w-14" />
        </div>
        <Skeleton className="h-3.5 w-24" />
      </div>
    </li>
  )
}
