import { AnimatePresence, motion } from 'motion/react'
import { useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router'
import { VerdictBlocked } from '@/components/illustrations'
import { Button, ButtonLink } from '../../components/Button.tsx'
import { CopyValue } from '../../components/CopyValue.tsx'
import { Icon } from '../../components/Icon.tsx'
import { rise, settle, stagger } from '../../components/motion.ts'
import { ErrorNote } from '../../components/Note.tsx'
import { PageBar, PageBody, PageHeader } from '../../components/PageBar.tsx'
import { SourceLine } from '../../components/Provenance.tsx'
import { Skeleton } from '../../components/Skeleton.tsx'
import { CodePill } from '../../components/Status.tsx'
import { CalendarDate } from '../../components/Time.tsx'
import { TONES } from '../../components/tones.ts'
import type { LookupRecord, NpiLookup, NpiRecord } from '../../lib/api.ts'
import { registryStatusLabel } from '../../lib/labels.ts'
import { draftFromRecord, matchesLine } from '../../lib/lookup.ts'
import { entityKindWord, type ProviderDraft } from '../../lib/provider.ts'
import { LOOKUP_LIMIT, useCheckNpiLive, useExclusionLookup, useNpi } from '../../lib/queries.ts'
import { isNpi, registryName, registryPlace } from '../../lib/registry.ts'
import { LIST, ListsLine, LookupRow, LookupRowSkeleton } from '../lookup/LookupRow.tsx'
import { LiveLine } from '../lookup/RegistryParts.tsx'
import { Fact } from '../provider/FactsPanel.tsx'
import { HeroFigure } from '../provider/VerdictHero.tsx'
import { AddProviderSheet } from '../providers/AddProviderSheet.tsx'
import { ProviderMark } from '../providers/ProviderMark.tsx'
import { draftFromNpi } from './draft.ts'

const BACK = { to: '/lookup', label: 'Lookup' }

/**
 * One NPI as the registry knows it: the backend's status first, with the way to track it as a
 * provider and to ask the live registry, then the exclusion lists that publish this NPI and
 * what NPPES records about it. A path that is not ten digits is not an NPI.
 */
export function NpiPage() {
  const { npi = '' } = useParams()
  return isNpi(npi) ? <Registered npi={npi} /> : <NotAnNpi />
}

function Registered({ npi }: { npi: string }) {
  const lookup = useNpi(npi)
  if (lookup.isError && !lookup.data) {
    return (
      <>
        <PageBar title="NPI" back={BACK} />
        <PageBody>
          <div className="flex flex-col items-start gap-6">
            <PageHeader title={`NPI ${npi}`} back={BACK} />
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
        </PageBody>
      </>
    )
  }
  return (
    <>
      <PageBar title="NPI" back={BACK} />
      <PageBody>
        {lookup.data ? <Loaded npi={npi} lookup={lookup.data} /> : <NpiSkeleton />}
      </PageBody>
    </>
  )
}

function Loaded({ npi, lookup }: { npi: string; lookup: NpiLookup }) {
  const [prefill, setPrefill] = useState<ProviderDraft | null>(null)
  const { record } = lookup
  const name = record && registryName(record)
  return (
    <>
      <div className="flex flex-col gap-10 lg:gap-14">
        <PageHeader
          back={BACK}
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
        <StatusHero
          npi={npi}
          lookup={lookup}
          onCheck={record ? () => setPrefill(draftFromNpi(record)) : null}
        />
        <div className="grid gap-12 lg:grid-cols-[minmax(0,1fr)_320px] lg:gap-10">
          <div className="min-w-0">
            <ExclusionLists npi={npi} onCheck={(found) => setPrefill(draftFromRecord(found))} />
          </div>
          {record && (
            <div className="lg:sticky lg:top-28 lg:self-start">
              <RegistryFacts record={record} />
            </div>
          )}
        </div>
      </div>
      <AnimatePresence>
        {prefill && <AddProviderSheet initial={prefill} onClose={() => setPrefill(null)} />}
      </AnimatePresence>
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
 * The answer first: the backend's status for this NPI on its tinted mark, the two actions, the
 * registry's dates and where the answer comes from. The stored file and a live answer each keep
 * their own line, so a cached answer never reads as live.
 */
function StatusHero({
  npi,
  lookup,
  onCheck,
}: {
  npi: string
  lookup: NpiLookup
  onCheck: (() => void) | null
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
            <Button
              variant="primary"
              icon="plus"
              spinIconOnHover
              onClick={onCheck ?? undefined}
              disabled={!onCheck}
              className="h-11 w-full sm:w-auto"
            >
              Check as provider
            </Button>
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

/** An NPI as a link to its registry page. */
function NpiLink({ npi }: { npi: string }) {
  return (
    <Link
      to={`/npi/${npi}`}
      className="rounded-sm font-mono font-medium text-accent-strong underline-offset-4 hover:underline"
    >
      {npi}
    </Link>
  )
}

/** The stored exclusion lists that publish this NPI, as the Lookup shows them. */
function ExclusionLists({
  npi,
  onCheck,
}: {
  npi: string
  onCheck: (record: LookupRecord) => void
}) {
  const lookup = useExclusionLookup(npi, null, true)
  const data = lookup.data
  return (
    <section aria-labelledby="lists-heading" aria-busy={!data && !lookup.isError}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 id="lists-heading" className="text-subheading">
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
                onCheck={() => onCheck(found)}
              />
            ))}
          </motion.ul>
        )}
      </div>
    </section>
  )
}

/** What NPPES records about the NPI, as registered; empty facts are left out. */
function RegistryFacts({ record }: { record: NpiRecord }) {
  const name = record.person_name
  const place = registryPlace(record)
  const taxonomies = [...record.taxonomies].sort((a, b) => Number(b.primary) - Number(a.primary))
  const official = record.authorized_official
  const dates: { term: string; value: string | null }[] = [
    { term: 'Enumerated', value: record.enumeration_date },
    { term: 'Last updated', value: record.last_updated },
    { term: 'Certified', value: record.certification_date },
    { term: 'Deactivated', value: record.deactivation_date },
    { term: 'Reactivated', value: record.reactivation_date },
  ]
  return (
    <section aria-labelledby="facts-heading">
      <h2 id="facts-heading" className="text-subheading">
        In NPPES
      </h2>
      <motion.dl
        variants={rise}
        initial="hidden"
        animate="shown"
        className="mt-4 divide-y divide-line rounded-card bg-surface px-5 py-1 shadow-card"
      >
        <Fact term="Kind">{entityKindWord(record.entity_kind)}</Fact>
        {name ? (
          <Fact term="Name">
            {record.name_prefix && `${record.name_prefix} `}
            <span className="font-semibold">{name.last}</span>
            {name.first && `, ${name.first}`}
            {name.middle && ` ${name.middle}`}
            {name.suffix && ` ${name.suffix}`}
          </Fact>
        ) : (
          record.organization_name && <Fact term="Organization">{record.organization_name}</Fact>
        )}
        {record.credential && <Fact term="Credential">{record.credential}</Fact>}
        {record.other_names.length > 0 && (
          <Fact term="Other names">
            <ul className="flex flex-col gap-1">
              {record.other_names.map((other) => (
                <li key={other}>{other}</li>
              ))}
            </ul>
          </Fact>
        )}
        {place && <Fact term="Practice place">{place}</Fact>}
        {taxonomies.length > 0 && (
          <Fact term="Taxonomies">
            <ul className="flex flex-col gap-1.5">
              {taxonomies.map((taxonomy, index) => (
                <li
                  key={`${taxonomy.code}-${index}`}
                  className="flex flex-wrap items-center gap-x-2 gap-y-1"
                >
                  <span className="font-mono text-small">{taxonomy.code}</span>
                  {taxonomy.license_state && <CodePill>{taxonomy.license_state}</CodePill>}
                  {taxonomy.license_number && (
                    <span className="font-mono text-small text-ink-muted">
                      {taxonomy.license_number}
                    </span>
                  )}
                  {taxonomy.primary && (
                    <span className="text-caption font-medium text-ink-muted">Primary</span>
                  )}
                </li>
              ))}
            </ul>
          </Fact>
        )}
        {official && (
          <Fact term="Authorized official">
            {[
              official.name.last,
              [official.name.first, official.name.middle].filter(Boolean).join(' '),
            ]
              .filter(Boolean)
              .join(', ')}
            {official.title && <span className="text-ink-muted"> · {official.title}</span>}
          </Fact>
        )}
        {dates.map(
          (item) =>
            item.value && (
              <Fact key={item.term} term={item.term}>
                <CalendarDate value={item.value} />
              </Fact>
            ),
        )}
        {record.replacement_npi && (
          <Fact term="Replacement NPI">
            {record.replacement_npi === record.npi ? (
              <span className="font-mono">{record.replacement_npi}</span>
            ) : (
              <NpiLink npi={record.replacement_npi} />
            )}
          </Fact>
        )}
        <Fact term="In latest file">{record.in_file ? 'Yes' : <InkNo />}</Fact>
      </motion.dl>
    </section>
  )
}

/** "No" for a row the latest full file no longer lists: worth noticing, so not muted. */
function InkNo() {
  return <span className="font-medium text-ink">No</span>
}

function NpiSkeleton() {
  return (
    <div aria-label="Loading NPI" className="flex flex-col gap-10 lg:gap-14">
      <div className="flex items-center gap-4 lg:mt-9">
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

/** A path that cannot be an NPI, in the not-found page's calm style. */
function NotAnNpi() {
  return (
    <>
      <PageBar title="Not an NPI" back={BACK} />
      <PageBody>
        <motion.div
          variants={stagger(0.06)}
          initial="hidden"
          animate="shown"
          className="flex min-h-[calc(100dvh-220px-env(safe-area-inset-top)-env(safe-area-inset-bottom))] flex-col items-center justify-center gap-2 pb-10 text-center lg:min-h-[calc(100dvh-212px)]"
        >
          <Rise>
            <span
              aria-hidden="true"
              className={`mb-6 grid size-20 place-items-center rounded-full ${TONES.neutral.tint} ${TONES.neutral.text}`}
            >
              <Icon name="registry" size={36} />
            </span>
          </Rise>
          <Rise>
            <h1 className="text-heading">Not an NPI</h1>
          </Rise>
          <Rise>
            <p className="text-body text-ink-muted">An NPI has ten digits.</p>
          </Rise>
          <Rise>
            <ButtonLink variant="primary" icon="arrowLeft" to="/lookup" className="mt-6 h-11 px-6">
              Back to Lookup
            </ButtonLink>
          </Rise>
        </motion.div>
      </PageBody>
    </>
  )
}

function Rise({ children }: { children: ReactNode }) {
  return <motion.div variants={rise}>{children}</motion.div>
}
