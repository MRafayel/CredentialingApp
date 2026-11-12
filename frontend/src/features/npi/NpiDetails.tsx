import { AnimatePresence, motion } from 'motion/react'
import { useId, useState } from 'react'
import { VerdictBlocked } from '@/components/illustrations'
import { Button } from '../../components/Button.tsx'
import { CopyValue } from '../../components/CopyValue.tsx'
import { settle, stagger } from '../../components/motion.ts'
import { ErrorNote } from '../../components/Note.tsx'
import { PageHeader } from '../../components/PageBar.tsx'
import { Skeleton } from '../../components/Skeleton.tsx'
import { SourceLine } from '../../components/Provenance.tsx'
import { CalendarDate } from '../../components/Time.tsx'
import { TONES } from '../../components/tones.ts'
import type { LookupRecord, NpiLookup } from '../../lib/api.ts'
import { registryStatusLabel } from '../../lib/labels.ts'
import { draftFromRecord, matchesLine } from '../../lib/lookup.ts'
import { entityKindWord, type ProviderDraft } from '../../lib/provider.ts'
import { LOOKUP_LIMIT, useCheckNpiLive, useExclusionLookup, useNpi } from '../../lib/queries.ts'
import { registryName } from '../../lib/registry.ts'
import { LIST, ListsLine, LookupRow, LookupRowSkeleton } from '../lookup/LookupRow.tsx'
import { LiveLine } from '../lookup/RegistryParts.tsx'
import { HeroFigure } from '../provider/VerdictHero.tsx'
import { AddProviderSheet } from '../providers/AddProviderSheet.tsx'
import { ProviderMark } from '../providers/ProviderMark.tsx'
import type { Back } from '../shell/back.ts'
import { draftFromNpi } from './draft.ts'
import { NpiLink, RegistryFacts } from './RegistryFacts.tsx'

const BACK: Back = { to: '/lookup', label: 'Lookup' }
/** In a dialog the title keeps clear of its X, which sits over the top right corner. */
const CLEAR_OF_CLOSE = 'pr-12'

/**
 * One NPI as the registry knows it: the backend's status first, with the way to ask the live
 * registry (and, on the registry page, to track it as a provider), then the exclusion lists that
 * publish this NPI and what NPPES records about it. `embedded` is the registry over a provider's
 * page: no back link, the title names the dialog (`titleId`), and no "Check as provider", since
 * the person is already looking at the provider. `back` is where the page's Back leads.
 */
export function NpiDetails({
  npi,
  embedded = false,
  titleId,
  back = BACK,
}: {
  npi: string
  embedded?: boolean
  titleId?: string
  back?: Back
}) {
  const lookup = useNpi(npi)
  if (lookup.isError && !lookup.data) {
    return (
      <div className={`flex flex-col items-start gap-6 ${embedded ? CLEAR_OF_CLOSE : ''}`}>
        <PageHeader title={`NPI ${npi}`} back={embedded ? undefined : back} titleId={titleId} />
        <ErrorNote
          action={
            <Button variant="ghost" className="-my-1 h-8" onClick={() => void lookup.refetch()}>
              Try again
            </Button>
          }
        >
          {lookup.error.message}
        </ErrorNote>
      </div>
    )
  }
  return lookup.data ? (
    <Loaded npi={npi} lookup={lookup.data} embedded={embedded} titleId={titleId} back={back} />
  ) : (
    <NpiSkeleton npi={npi} embedded={embedded} titleId={titleId} />
  )
}

function Loaded({
  npi,
  lookup,
  embedded,
  titleId,
  back,
}: {
  npi: string
  lookup: NpiLookup
  embedded: boolean
  titleId: string | undefined
  back: Back
}) {
  // Came from a provider's page (or sits over one): no "Check as provider", it is that provider.
  const ownProvider = embedded || back.to.startsWith('/providers/')
  const [prefill, setPrefill] = useState<ProviderDraft | null>(null)
  const { record } = lookup
  const name = record && registryName(record)
  return (
    <>
      <div className="flex flex-col gap-10 lg:gap-14">
        <div className={embedded ? CLEAR_OF_CLOSE : undefined}>
          <PageHeader
            back={embedded ? undefined : back}
            titleId={titleId}
            lead={
              record && (
                <ProviderMark
                  provider={{ id: `npi-${npi}`, entity_kind: record.entity_kind }}
                  size="lg"
                />
              )
            }
            title={
              name ?? (
                <span className="text-ink-muted">
                  {record ? 'Name not published' : 'No registry record'}
                </span>
              )
            }
            subtitle={
              <span className="inline-flex flex-wrap items-center gap-x-1.5">
                {record && (
                  <>
                    <span>{entityKindWord(record.entity_kind)}</span>
                    <span aria-hidden="true">·</span>
                  </>
                )}
                <span>NPI</span>
                <CopyValue value={npi} label="NPI" className="-ml-1 text-body" />
              </span>
            }
          />
        </div>
        <StatusHero
          npi={npi}
          lookup={lookup}
          onCheck={ownProvider ? null : record ? () => setPrefill(draftFromNpi(record)) : undefined}
        />
        <div className="grid gap-12 lg:grid-cols-[minmax(0,1fr)_320px] lg:gap-10">
          <div className="min-w-0">
            <ExclusionLists
              npi={npi}
              onCheck={ownProvider ? undefined : (found) => setPrefill(draftFromRecord(found))}
            />
          </div>
          {record && (
            // A column taller than the dialog cannot stick inside it; on the page it can.
            <div className={embedded ? '' : 'lg:sticky lg:top-28 lg:self-start'}>
              <RegistryFacts record={record} />
            </div>
          )}
        </div>
      </div>
      {!embedded && (
        <AnimatePresence>
          {prefill && <AddProviderSheet initial={prefill} onClose={() => setPrefill(null)} />}
        </AnimatePresence>
      )}
    </>
  )
}

/** The status's one line under the word: when and by what it was deactivated, or why not known. */
function StatusDetail({ lookup }: { lookup: NpiLookup }) {
  const { status, record, live } = lookup
  switch (status) {
    case 'deactivated':
      return record?.deactivation_date || record?.replacement_npi ? (
        <>
          {record.deactivation_date && (
            <>
              Since <CalendarDate value={record.deactivation_date} />
            </>
          )}
          {record.deactivation_date && record.replacement_npi && ' · '}
          {record.replacement_npi && (
            <>
              {record.deactivation_date ? 'replaced by ' : 'Replaced by '}
              <NpiLink npi={record.replacement_npi} />
            </>
          )}
        </>
      ) : null
    case 'not_found':
      return <>The live registry returned no result</>
    case 'mirror_only_active':
      return <>Active in the NPPES file · the live registry returned no result</>
    case 'unknown':
      return <>{live?.note ?? 'No answer from the registry'}</>
    case 'active':
      return null
  }
}

/**
 * The answer first: the backend's status for this NPI on its tinted mark, its actions, the
 * registry's dates and where the answer comes from. The stored file and a live answer each keep
 * their own line, so a cached answer never reads as live. `onCheck` is "Check as provider":
 * `undefined` shows it disabled (no record to prefill), `null` leaves it out.
 */
function StatusHero({
  npi,
  lookup,
  onCheck,
}: {
  npi: string
  lookup: NpiLookup
  onCheck: (() => void) | null | undefined
}) {
  const live = useCheckNpiLive(npi)
  const label = registryStatusLabel(lookup.status)
  const tone = TONES[label.tone]
  const { record, mirror } = lookup
  const answer = `${lookup.status}-${lookup.live?.provenance.verified_at ?? 'file'}`
  const primary = record?.taxonomies.find((taxonomy) => taxonomy.primary)?.code
  return (
    <div className="flex flex-col gap-3">
      <section
        aria-label="Registry status"
        aria-live="polite"
        className="rounded-hero bg-surface p-6 shadow-card sm:p-8 lg:p-10"
      >
        <div className="flex flex-col gap-6 sm:flex-row sm:items-center">
          <AnimatePresence mode="popLayout" initial={false}>
            <motion.div
              key={answer}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={settle}
              className="flex min-w-0 flex-1 items-center gap-5"
            >
              <motion.span
                initial={{ opacity: 0, scale: 0.85 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={settle}
                className={`grid size-20 shrink-0 place-items-center rounded-full sm:size-24 ${tone.tint} ${tone.text}`}
              >
                <tone.Mark size={52} className="max-sm:size-11" />
              </motion.span>
              <div className="min-w-0">
                <p className="text-display">{label.word}</p>
                <p className="mt-1 text-body break-words text-ink-muted empty:hidden">
                  <StatusDetail lookup={lookup} />
                </p>
              </div>
            </motion.div>
          </AnimatePresence>
          <div className="flex flex-col gap-3 sm:flex-row-reverse sm:items-center sm:self-center">
            {onCheck !== null && (
              <Button
                variant="primary"
                icon="plus"
                spinIconOnHover
                onClick={onCheck}
                disabled={!onCheck}
                className="h-11 w-full sm:w-auto"
              >
                Check as provider
              </Button>
            )}
            <Button
              variant="secondary"
              icon="bolt"
              onClick={() => live.mutate()}
              disabled={live.isPending}
              aria-busy={live.isPending}
              className="h-11 w-full sm:w-auto"
            >
              {live.isPending ? 'Checking…' : 'Check live'}
            </Button>
          </div>
        </div>
        <dl className="mt-6 grid divide-y divide-line border-t border-line sm:mt-8 sm:grid-cols-3 sm:divide-x sm:divide-y-0 sm:pt-6 lg:mt-10">
          <HeroFigure term="Enumerated" first>
            {record?.enumeration_date ? <CalendarDate value={record.enumeration_date} /> : '—'}
          </HeroFigure>
          <HeroFigure term="Last updated">
            {record?.last_updated ? <CalendarDate value={record.last_updated} /> : '—'}
          </HeroFigure>
          <HeroFigure term="Taxonomy">
            {primary ? <span className="font-mono">{primary}</span> : '—'}
          </HeroFigure>
        </dl>
        <div className="mt-5 flex flex-col gap-1">
          {mirror ? (
            <SourceLine block={mirror} kind="mirror" />
          ) : (
            <p className="inline-flex items-center gap-1.5 text-small font-semibold text-ink">
              <VerdictBlocked size={13} className="shrink-0" />
              The registry mirror is not loaded
            </p>
          )}
          <AnimatePresence mode="popLayout" initial={false}>
            {lookup.live && (
              <motion.span
                key={lookup.live.provenance.verified_at}
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={settle}
              >
                <LiveLine live={lookup.live} />
              </motion.span>
            )}
          </AnimatePresence>
        </div>
      </section>
      {live.isError && <ErrorNote>{live.error.message}</ErrorNote>}
    </div>
  )
}

/**
 * The stored exclusion lists that publish this NPI, as the Lookup shows them; each row offers
 * "Check as provider" only when `onCheck` is given.
 */
function ExclusionLists({
  npi,
  onCheck,
}: {
  npi: string
  onCheck?: (record: LookupRecord) => void
}) {
  const lookup = useExclusionLookup(npi, null, true)
  const data = lookup.data
  const headingId = useId()
  return (
    <section aria-labelledby={headingId} aria-busy={!data && !lookup.isError}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 id={headingId} className="text-subheading">
          Exclusion lists
        </h2>
        {data && data.records.length > 0 && (
          <span className="text-body text-ink-muted tabular-nums">
            {matchesLine(data.records.length, LOOKUP_LIMIT)}
          </span>
        )}
      </div>
      {data && <ListsLine sources={data.sources} state={data.state} className="mt-1" />}
      <div className="mt-4">
        {lookup.isError && !data ? (
          <ErrorNote
            action={
              <Button variant="ghost" className="-my-1 h-8" onClick={() => void lookup.refetch()}>
                Try again
              </Button>
            }
          >
            {lookup.error.message}
          </ErrorNote>
        ) : !data ? (
          <ul aria-label="Searching the exclusion lists" className={LIST}>
            <LookupRowSkeleton />
            <LookupRowSkeleton />
          </ul>
        ) : data.records.length === 0 ? (
          <motion.p
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={settle}
            className="flex items-center gap-2 text-body font-medium text-ink"
          >
            <span
              className={`grid size-7 shrink-0 place-items-center rounded-full ${TONES.clear.tint} ${TONES.clear.text}`}
            >
              <TONES.clear.Mark size={15} />
            </span>
            Not on any stored exclusion list
          </motion.p>
        ) : (
          <motion.ul variants={stagger()} initial="hidden" animate="shown" className={LIST}>
            {data.records.map((found) => (
              <LookupRow
                key={found.id}
                record={found}
                sourceName={
                  data.sources.find((source) => source.source_id === found.source_id)
                    ?.display_name ?? found.source_id
                }
                onCheck={onCheck && (() => onCheck(found))}
              />
            ))}
          </motion.ul>
        )}
      </div>
    </section>
  )
}

function NpiSkeleton({
  npi,
  embedded,
  titleId,
}: {
  npi: string
  embedded: boolean
  titleId: string | undefined
}) {
  return (
    <div aria-label="Loading NPI" className="flex flex-col gap-10 lg:gap-14">
      {/* A dialog is named by its title even before the record arrives. */}
      {titleId && (
        <h2 id={titleId} className="sr-only">
          NPI {npi}
        </h2>
      )}
      {/* On the page the back link sits above the title; a dialog has none. */}
      <div className={`flex items-center gap-4 ${embedded ? '' : 'lg:mt-9'}`}>
        <Skeleton className="size-12 rounded-full lg:size-16" />
        <div className="flex flex-col gap-3">
          <Skeleton className="h-8 w-72 max-w-full lg:h-11" />
          <Skeleton className="h-4 w-48" />
        </div>
      </div>
      <Skeleton className="h-[260px] w-full rounded-hero" />
      <div className="grid gap-12 lg:grid-cols-[minmax(0,1fr)_320px] lg:gap-10">
        <div className="flex flex-col gap-4">
          <Skeleton className="h-6 w-40" />
          <Skeleton className="h-28 w-full rounded-item" />
        </div>
        <Skeleton className="h-96 w-full rounded-card" />
      </div>
    </div>
  )
}
