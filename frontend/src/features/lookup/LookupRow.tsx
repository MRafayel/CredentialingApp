import { motion } from 'motion/react'
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { VerdictBlocked } from '@/components/illustrations'
import { Button } from '../../components/Button.tsx'
import { CopyValue } from '../../components/CopyValue.tsx'
import { rise } from '../../components/motion.ts'
import { Skeleton } from '../../components/Skeleton.tsx'
import { StatusLabel } from '../../components/Status.tsx'
import { CalendarDate } from '../../components/Time.tsx'
import type { LookupRecord, SearchedSource } from '../../lib/api.ts'
import {
  actionLabel,
  notLoadedText,
  placeText,
  recordLabel,
  searchedScope,
} from '../../lib/lookup.ts'
import { displayName } from '../../lib/provider.ts'
import { CARD, CARD_LINK } from './cards.ts'
import { stateName } from './map/mapInfo.ts'

/*
 * A listed record as the lookup shows it, shared by the Lookup page and the registry page: the
 * card, its skeleton, and the line saying where a search looked.
 */

/** Results: a card per record, so each one reads on its own. */
export const LIST = 'flex flex-col gap-3'

/**
 * Identity, status, identifiers, action. Fractions after one auto column (the same button in
 * every card), so the columns line up from card to card.
 */
const COLUMNS =
  'flex flex-col gap-4 p-5 lg:grid lg:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)_minmax(0,1fr)_auto] lg:items-center lg:gap-8 lg:px-7 lg:py-6'

/**
 * "Searched 9 states and the federal list": where an answer looked. Lists never loaded are
 * named as such, never counted as searched.
 */
export function ListsLine({
  sources,
  state,
  registry = false,
  className,
}: {
  sources: readonly SearchedSource[]
  /** The state the search was narrowed to, from the response. */
  state: string | null
  /** Whether the NPI registry was searched beside the lists, so the line names it too. */
  registry?: boolean
  className?: string
}) {
  const scope = searchedScope(
    sources,
    state ? { code: state, name: stateName(state) } : null,
    registry,
  )
  return (
    <p className={`text-caption text-ink-muted ${className ?? ''}`}>
      {scope.where}
      {scope.notLoaded > 0 && (
        <>
          {' · '}
          <span className="inline-flex items-center gap-1 font-semibold text-ink">
            <VerdictBlocked size={12} className="shrink-0" />
            {notLoadedText(scope.notLoaded)}
          </span>
        </>
      )}
    </p>
  )
}

/**
 * One listed record as a card that opens the record. Desktop reads across, in columns that
 * line up between cards: who (source, name, place), what (the action on its tinted mark, then
 * its dates), which (NPI, provider type, licence), then the one action. Phone stacks it.
 */
export function LookupRow({
  record,
  sourceName,
  onCheck,
}: {
  record: LookupRecord
  sourceName: string
  onCheck: () => void
}) {
  const label = recordLabel(record)
  const place = placeText(record)
  const extras: { key: string; node: ReactNode }[] = []
  if (place) extras.push({ key: 'place', node: place })
  if (record.aliases.length > 0) {
    extras.push({ key: 'aliases', node: `Also known as ${record.aliases.join('; ')}` })
  }
  if (record.related_parties.length > 0) {
    extras.push({ key: 'related', node: `Named with ${record.related_parties.join('; ')}` })
  }

  return (
    <motion.li variants={rise} className={CARD}>
      <div className={COLUMNS}>
        <div className="min-w-0">
          <p className="truncate text-[11px] leading-4 font-semibold tracking-[0.06em] text-ink-muted uppercase">
            {sourceName}
          </p>
          <h3 className="mt-1.5 text-[17px] leading-snug font-semibold break-words text-ink">
            <Link to={`/lookup/${record.id}`} className={CARD_LINK}>
              {displayName(record)}
            </Link>
          </h3>
          {extras.length > 0 && (
            <p className="mt-0.5 text-small break-words text-ink-muted">
              {extras.map((item, index) => (
                <span key={item.key}>
                  {index > 0 && ' · '}
                  {item.node}
                </span>
              ))}
            </p>
          )}
        </div>
        <div className="flex flex-col gap-1 text-small">
          <StatusLabel label={label} size={15} chip className="text-body" />
          <div className="flex flex-col gap-0.5 pl-9 text-ink-muted">
            {record.status !== 'active' && <span>{actionLabel(record.action).word}</span>}
            {record.action_raw && <span>As published: {record.action_raw}</span>}
            <span className="tabular-nums">
              Effective{' '}
              {record.effective_date ? (
                <CalendarDate value={record.effective_date} />
              ) : (
                'not published'
              )}
            </span>
            {record.end_date && (
              <span className="tabular-nums">
                Ended <CalendarDate value={record.end_date} />
              </span>
            )}
          </div>
        </div>
        <div className="flex min-w-0 flex-col gap-0.5 text-small text-ink-muted">
          <span className="inline-flex items-center gap-1">
            NPI
            {record.npi ? (
              <CopyValue value={record.npi} label="NPI" className="relative z-10 -my-1.5" />
            ) : (
              <span>not published</span>
            )}
          </span>
          {record.raw_provider_type && (
            <span className="break-words">{record.raw_provider_type}</span>
          )}
          {record.license && (
            <span>
              Licence <span className="font-mono">{record.license.number}</span> ·{' '}
              {record.license.state}
            </span>
          )}
        </div>
        <div className="relative z-10 self-start lg:self-auto">
          <Button variant="primary" icon="plus" spinIconOnHover onClick={onCheck}>
            Check as provider
          </Button>
        </div>
      </div>
    </motion.li>
  )
}

export function LookupRowSkeleton() {
  return (
    <li className="rounded-item bg-surface shadow-card">
      <div className={COLUMNS}>
        <div className="flex flex-col gap-2">
          <Skeleton className="h-3 w-32" />
          <Skeleton className="h-4 w-56 max-w-full" />
        </div>
        <div className="flex items-center gap-2">
          <Skeleton className="size-7 rounded-full" />
          <Skeleton className="h-3.5 w-24" />
        </div>
        <Skeleton className="h-3.5 w-36" />
        <Skeleton className="h-10 w-44 rounded-full" />
      </div>
    </li>
  )
}
