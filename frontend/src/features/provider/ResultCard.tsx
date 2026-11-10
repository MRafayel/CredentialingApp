import { AnimatePresence, motion } from 'motion/react'
import { useId, useState, type ReactNode } from 'react'
import { VerdictBlocked, VerdictListed } from '@/components/illustrations'
import { Icon } from '../../components/Icon.tsx'
import { settle } from '../../components/motion.ts'
import { Skeleton } from '../../components/Skeleton.tsx'
import { Freshness, StatusLabel } from '../../components/Status.tsx'
import { Ago, CalendarDate, DateOf } from '../../components/Time.tsx'
import type {
  MatchedRecord,
  MatchLevel,
  Provider,
  RegistryStatus,
  Verification,
} from '../../lib/api.ts'
import { displayName, entityKindWord } from '../../lib/provider.ts'
import {
  freshnessLabel,
  matchLevelLabel,
  outcomeWord,
  registryStatusLabel,
  resultLabel,
} from '../../lib/labels.ts'

const FIELD_WORDS: Record<string, string> = {
  npi: 'NPI',
  last_name: 'Last name',
  first_name: 'First name',
  middle_name: 'Middle name',
  date_of_birth: 'Date of birth',
  license: 'Licence',
  address: 'Address',
  organization_name: 'Organization name',
}

const DETAIL_WORDS: Record<string, string> = {
  list_fetched_at: 'List fetched',
  candidates_compared: 'Candidates compared',
  latest_fetch_outcome: 'Latest fetch',
  latest_fetch_at: 'Latest fetch at',
  record_status: 'Record status',
  // An NPI verification's details.
  npi_status: 'Registry status',
  name_match: 'Name match',
  entity_kind: 'Kind in NPPES',
  checked_live: 'Live registry answered',
  registry_last_updated: 'Record last updated',
  live_check_outcome: 'Live check',
  live_check_at: 'Live check at',
  npi_set_by: 'NPI set by',
  npi_set_at: 'NPI set at',
  reason: 'Reason',
}

const REGISTRY_STATUSES: readonly string[] = [
  'active',
  'deactivated',
  'not_found',
  'mirror_only_active',
  'unknown',
] satisfies RegistryStatus[]
const MATCH_LEVELS: readonly string[] = [
  'exact',
  'high',
  'potential',
  'ambiguous',
  'none',
] satisfies MatchLevel[]
const KINDS: readonly string[] = [
  'individual',
  'organization',
  'unknown',
] satisfies Provider['entity_kind'][]

const isRegistryStatus = (value: string): value is RegistryStatus =>
  REGISTRY_STATUSES.includes(value)
const isMatchLevel = (value: string): value is MatchLevel => MATCH_LEVELS.includes(value)
const isKind = (value: string): value is Provider['entity_kind'] => KINDS.includes(value)

/** A detail's value in words; the backend's enum values are named as the labels name them. */
function DetailValue({ name, value }: { name: string; value: string }) {
  if (name.endsWith('_at')) return <DateOf at={value} />
  if (name === 'registry_last_updated') return <CalendarDate value={value} />
  if (name.endsWith('_outcome')) return outcomeWord(value)
  if (name === 'npi_status' && isRegistryStatus(value)) return registryStatusLabel(value).word
  if (name === 'name_match' && isMatchLevel(value)) return matchLevelLabel(value).word
  if (name === 'entity_kind' && isKind(value)) return entityKindWord(value)
  if (name === 'checked_live') return value === 'true' ? 'Yes' : 'No'
  if (name === 'npi_set_by' && value === 'user') return 'The user'
  return value
}

/** One source's answer: status, source, time, the backend's sentence, and the evidence. */
export function ResultCard({ result, sourceName }: { result: Verification; sourceName: string }) {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  const label = resultLabel(result)
  const updated = result.provenance.source_updated_at
  const reason = result.details.latest_fetch_outcome

  return (
    <article className="rounded-card bg-surface shadow-card">
      <div className="flex flex-col gap-3 p-5 sm:p-6">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-small">
          <StatusLabel label={label} chip className="text-body" />
          <span className="flex flex-wrap items-center gap-x-1.5 text-ink-muted">
            <Freshness label={freshnessLabel(result.freshness)} />
            <span aria-hidden="true">·</span>
            {updated ? (
              <span>
                List updated <DateOf at={updated} />
              </span>
            ) : (
              <span>
                {result.outcome === 'completed'
                  ? 'List date not stated'
                  : 'List updated not published'}
              </span>
            )}
          </span>
          <span className="text-ink-muted sm:ml-auto">
            Checked <Ago at={result.provenance.verified_at} />
          </span>
        </div>
        <h3 className="text-title">{sourceName}</h3>
        <p className="max-w-[72ch] text-body text-ink-muted">{result.summary}</p>
        {result.outcome !== 'completed' && reason && (
          <p className="flex items-center gap-1.5 text-small font-medium text-ink">
            <VerdictBlocked size={14} className="shrink-0" />
            Latest fetch: {outcomeWord(reason)}
            {result.details.latest_fetch_at && (
              <>
                {' · '}
                <Ago at={result.details.latest_fetch_at} />
              </>
            )}
          </p>
        )}
        <div className="flex items-center gap-2 pt-1">
          <a
            href={result.provenance.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex h-8 items-center gap-1 rounded-full text-small font-medium text-accent-strong underline-offset-4 hover:underline"
          >
            Official page
            <Icon name="external" size={14} />
            <span className="sr-only">(opens in a new tab)</span>
          </a>
          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            aria-controls={panelId}
            className="-mr-2 ml-auto inline-flex h-8 items-center gap-1 rounded-full px-2 text-small font-medium text-ink-muted transition-colors hover:text-ink"
          >
            {open ? 'Hide details' : 'Details'}
            <motion.span animate={{ rotate: open ? 180 : 0 }} transition={settle}>
              <Icon name="chevronDown" size={15} />
            </motion.span>
          </button>
        </div>
      </div>
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
            <Evidence result={result} />
          </motion.div>
        )}
      </AnimatePresence>
    </article>
  )
}

/** A result's evidence, its matched records and its details; `children` close the panel. */
export function Evidence({ result, children }: { result: Verification; children?: ReactNode }) {
  const details = Object.entries(result.details)
  return (
    <div className="flex flex-col gap-5 border-t border-line px-5 py-5 sm:px-6">
      {result.matched_records.length > 0 && (
        <section>
          <h4 className="text-label">Matched records</h4>
          <ul className="mt-2 divide-y divide-line border-y border-line">
            {result.matched_records.map((record) => (
              <MatchedRecordItem key={record.id} record={record} />
            ))}
          </ul>
        </section>
      )}
      {result.match && (
        <section>
          <h4 className="text-label">Match evidence</h4>
          <ul className="mt-2 flex flex-col gap-1">
            {result.match.evidence.map((item, index) => (
              <li key={`${item.field}-${index}`} className="flex items-baseline gap-2 text-small">
                <span
                  aria-hidden="true"
                  className={`w-4 shrink-0 text-center font-bold ${item.matched ? 'text-status-clear' : 'text-status-listed'}`}
                >
                  {item.matched ? '✓' : '✕'}
                </span>
                <span className="font-medium">{FIELD_WORDS[item.field] ?? item.field}</span>
                <span className="sr-only">{item.matched ? 'matched' : 'did not match'}</span>
                {item.detail && <span className="min-w-0 text-ink-muted">{item.detail}</span>}
              </li>
            ))}
          </ul>
          <p className="mt-2 text-small text-ink">{result.match.explanation}</p>
        </section>
      )}
      {details.length > 0 && (
        <dl className="divide-y divide-line border-y border-line text-small">
          {details.map(([key, value]) => (
            <div key={key} className="flex items-baseline justify-between gap-4 py-2.5">
              <dt className="text-ink-muted">{DETAIL_WORDS[key] ?? key.replaceAll('_', ' ')}</dt>
              <dd className="min-w-0 text-right break-words tabular-nums">
                <DetailValue name={key} value={value} />
              </dd>
            </div>
          ))}
        </dl>
      )}
      {children}
    </div>
  )
}

function MatchedRecordItem({ record }: { record: MatchedRecord }) {
  return (
    <li className="py-3 text-small">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-semibold">{displayName(record)}</span>
        <span className="inline-flex items-center gap-1.5 font-medium">
          <VerdictListed size={14} className="shrink-0" />
          {record.action_raw ?? record.action}
        </span>
        {record.npi && <span className="font-mono text-ink-muted">{record.npi}</span>}
      </div>
      <p className="mt-0.5 text-ink-muted">
        Effective {record.effective_date ? <CalendarDate value={record.effective_date} /> : '—'}
        {' · '}
        Ended {record.end_date ? <CalendarDate value={record.end_date} /> : '—'}
      </p>
    </li>
  )
}

/** A result card's shape while its source is being checked. */
export function ResultCardSkeleton({ sourceName }: { sourceName: string }) {
  return (
    <div className="rounded-card bg-surface p-5 shadow-card sm:p-6">
      <div className="flex items-center gap-3">
        <Skeleton className="size-7 rounded-full" />
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-3.5 w-40" />
      </div>
      <h3 className="mt-3 text-title text-ink-muted">{sourceName}</h3>
      <Skeleton className="mt-3 h-4 w-full max-w-md" />
      <Skeleton className="mt-2 h-4 w-2/3 max-w-xs" />
      <Skeleton className="mt-5 h-3.5 w-28" />
    </div>
  )
}
