import { AnimatePresence, LayoutGroup, motion, useReducedMotion } from 'motion/react'
import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { Navigate, useMatch, useNavigate, useParams } from 'react-router'
import { ButtonLink } from '../../components/Button.tsx'
import { CopyValue } from '../../components/CopyValue.tsx'
import { Modal } from '../../components/Modal.tsx'
import { ErrorNote } from '../../components/Note.tsx'
import { PageBar, PageBody, PageHeader } from '../../components/PageBar.tsx'
import { Skeleton } from '../../components/Skeleton.tsx'
import { useNow } from '../../components/useNow.ts'
import { StatusLabel } from '../../components/Status.tsx'
import { ApiError, type Provider, type SourceStatus } from '../../lib/api.ts'
import { plural } from '../../lib/format.ts'
import { identityLabel, resultLabel } from '../../lib/labels.ts'
import { providerSteps, type StepAction } from '../../lib/monitoring.ts'
import { displayName } from '../../lib/provider.ts'
import { needsAttention } from '../../lib/results.ts'
import {
  useHistory,
  useLatestNpiVerification,
  useMarkSeen,
  useNpi,
  useProvider,
  useProviderStatus,
  useProviderSummary,
  useRunCheck,
  useSources,
  useVerdict,
} from '../../lib/queries.ts'
import { RecordDetails } from '../lookup/RecordDetails.tsx'
import { NpiDetails } from '../npi/NpiDetails.tsx'
import { ProviderMark } from '../providers/ProviderMark.tsx'
import { Here, type Back } from '../shell/back.ts'
import { listCaption, listedRecords, type ListedRecord } from './facts.ts'
import { FactsPanel } from './FactsPanel.tsx'
import { npiShape } from './npi.ts'
import { NpiCard, NpiCardSkeleton } from './NpiCard.tsx'
import { ResultCard, ResultCardSkeleton } from './ResultCard.tsx'
import { Steps, StepsSkeleton } from './Steps.tsx'
import { Timeline } from './Timeline.tsx'
import { timelineEntries } from './timeline.ts'
import { VerdictHero } from './VerdictHero.tsx'

const BACK: Back = { to: '/providers', label: 'Providers' }
/** An exclusion record over a provider's page. */
const recordPath = (providerId: string, recordId: string) =>
  `/providers/${providerId}/record/${recordId}`
/** The exclusion record's own page, as the Lookup opens it. */
const recordPage = (recordId: string) => `/lookup/${recordId}`
/** The NPI card, where every Identity action leads. */
const NPI_CARD_ID = 'npi-card'
const CARD_STAGGER = 0.08
/** The sections under the NPI card glide when it changes shape. */
const FOLLOW = { layout: { duration: 0.36, ease: [0.22, 1, 0.36, 1] as const } }

export function ProviderPage() {
  const { id = '' } = useParams()
  const provider = useProvider(id)

  if (provider.isError && !provider.data) {
    const missing = provider.error instanceof ApiError && provider.error.status === 404
    return (
      <>
        <PageBar title={missing ? 'Not found' : 'Provider'} back={BACK} />
        <PageBody>
          <div className="flex flex-col items-start gap-6">
            <PageHeader title={missing ? 'Not found' : 'Provider'} back={BACK} />
            <ErrorNote>{provider.error.message}</ErrorNote>
            <ButtonLink to="/providers" icon="arrowLeft">
              Back to providers
            </ButtonLink>
          </div>
        </PageBody>
      </>
    )
  }

  return (
    <>
      <PageBar title="Provider" back={BACK} />
      <PageBody>
        {provider.data ? (
          // Every record or registry page opened from here, or from a dialog over it, leads back
          // here, never to a dialog's own address (owner, 2026-11-12).
          <Here value={{ to: `/providers/${provider.data.id}`, label: displayName(provider.data) }}>
            <Loaded provider={provider.data} />
          </Here>
        ) : (
          <HeaderSkeleton />
        )}
      </PageBody>
    </>
  )
}

function Loaded({ provider }: { provider: Provider }) {
  const verdict = useVerdict(provider.id)
  const history = useHistory(provider.id)
  const latestNpi = useLatestNpiVerification(provider.id)
  const sources = useSources()
  const run = useRunCheck(provider.id)

  const byId = new Map<string, SourceStatus>(
    (sources.data ?? []).map((source) => [source.source_id, source]),
  )
  const sourceName = (sourceId: string) => byId.get(sourceId)?.display_name ?? sourceId
  const latest = verdict.data?.results ?? []
  const listed = listedRecords(latest, (sourceId) => listCaption(byId.get(sourceId), sourceId))
  const shown = latest.filter(needsAttention)
  // A check consults the exclusion lists; the NPI registry files are listed too but not checked.
  const expected = (sources.data ?? []).filter((source) => source.credential_type === 'exclusion')
  const running = run.isPending
  const npi = npiShape(provider.npi, latestNpi.data ?? null)
  const status = useProviderStatus(provider.id)
  // The recheck interval is the backend's policy; the roster's summary carries it.
  const intervalDays = useProviderSummary().data?.recheck_interval_days
  const now = useNow()
  const changedOnOpen = useSeenOnOpen(provider.id, status)
  const changedAt = changedOnOpen ? (status.data?.changed_at ?? null) : null
  const reduced = useReducedMotion() ?? false
  const recordHref = (recordId: string) => recordPath(provider.id, recordId)

  // Every step action is the NPI card's: find, verify or review the NPI there.
  function act(_action: StepAction) {
    const card = document.getElementById(NPI_CARD_ID)
    card?.scrollIntoView({ behavior: reduced ? 'instant' : 'smooth', block: 'start' })
    card?.focus({ preventScroll: true })
  }

  return (
    <div className="flex flex-col gap-10 lg:gap-14">
      <PageHeader
        back={BACK}
        lead={<ProviderMark provider={provider} size="lg" />}
        title={
          <motion.span layoutId={`provider-name-${provider.id}`} className="inline-block">
            {displayName(provider)}
          </motion.span>
        }
        subtitle={
          <span className="flex flex-wrap items-center gap-x-2">
            <span>{provider.entity_kind === 'organization' ? 'Organization' : 'Individual'}</span>
            {provider.npi && (
              <>
                <span aria-hidden="true" className="max-sm:hidden">
                  ·
                </span>
                {/* The NPI and its registry status wrap together (on a phone, on their own line). */}
                <span className="inline-flex flex-wrap items-center gap-x-2 max-sm:basis-full">
                  <span className="inline-flex items-center gap-1">
                    NPI
                    <CopyValue value={provider.npi} label="NPI" />
                  </span>
                  {npi.kind === 'verified' && (
                    <>
                      <span aria-hidden="true">·</span>
                      <StatusLabel label={resultLabel(npi.result)} size={14} />
                    </>
                  )}
                </span>
              </>
            )}
          </span>
        }
      />

      <div className="flex flex-col gap-8 lg:gap-10">
        <div className="flex flex-col gap-3">
          {status.data && verdict.data && intervalDays !== undefined ? (
            <Steps
              steps={providerSteps(status.data, verdict.data, { now, intervalDays })}
              onAction={act}
            />
          ) : (
            !(status.isError || verdict.isError) && <StepsSkeleton />
          )}
          {status.isError && <ErrorNote>{status.error.message}</ErrorNote>}
        </div>

        <div className="flex flex-col gap-3">
          {verdict.data ? (
            <VerdictHero
              verdict={verdict.data}
              expected={expected.length}
              changedAt={changedAt}
              running={running}
              onRun={() => run.mutate()}
            />
          ) : (
            !verdict.isError && <Skeleton className="h-[260px] w-full rounded-hero" />
          )}
          <AnimatePresence>
            {run.isError && (
              <motion.div
                initial={{ opacity: 0, y: -6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
              >
                <ErrorNote>{run.error.message}</ErrorNote>
              </motion.div>
            )}
          </AnimatePresence>
          {verdict.isError && <ErrorNote>{verdict.error.message}</ErrorNote>}
        </div>
      </div>

      {/* On desktop both columns span two rows, a heading row and a content row, so the NPI
          card and the Facts card start on the same line (the left heading cell is empty). */}
      <div className="grid gap-12 lg:grid-cols-[minmax(0,1fr)_320px] lg:grid-rows-[auto_1fr] lg:gap-x-10 lg:gap-y-4">
        <LayoutGroup>
          <div className="min-w-0 lg:row-span-2 lg:grid lg:grid-rows-subgrid">
            {/* The section's heading, level with "Facts" on desktop: the identity in a word
                and a line, as the step above says it, so the card reads on its own. */}
            <div className="mb-4 flex flex-wrap items-baseline gap-x-3 gap-y-1 lg:mb-0">
              <h2 className="text-subheading">Identity</h2>
              {status.data && (
                <StatusLabel
                  label={identityLabel(status.data.identity)}
                  size={14}
                  className="text-small"
                />
              )}
            </div>
            <div className="flex min-w-0 flex-col gap-10 lg:gap-12">
              {provider.npi && latestNpi.data === undefined ? (
                latestNpi.isError ? (
                  <ErrorNote>{latestNpi.error.message}</ErrorNote>
                ) : (
                  <NpiCardSkeleton />
                )
              ) : (
                <NpiCard id={NPI_CARD_ID} provider={provider} latest={latestNpi.data ?? null} />
              )}
              <motion.section
                layout="position"
                transition={FOLLOW}
                aria-labelledby="results-heading"
                aria-live="polite"
                aria-busy={running}
                className="flex flex-col gap-3"
              >
                <h2 id="results-heading" className="mb-2 text-subheading">
                  Results
                </h2>
                <AnimatePresence mode="popLayout" initial={false}>
                  {running
                    ? (expected.length ? expected : [null]).map((source, index) => (
                        <motion.div
                          key={`checking-${source?.source_id ?? index}`}
                          initial={{ opacity: 0 }}
                          animate={{ opacity: 1 }}
                          exit={{ opacity: 0 }}
                          transition={{ duration: 0.2 }}
                        >
                          <ResultCardSkeleton sourceName={source?.display_name ?? 'Checking…'} />
                        </motion.div>
                      ))
                    : shown.map((result, index) => (
                        <motion.div
                          key={result.id}
                          layout="position"
                          initial={{ opacity: 0, y: 10 }}
                          animate={{ opacity: 1, y: 0 }}
                          exit={{ opacity: 0 }}
                          transition={{
                            duration: 0.26,
                            ease: [0.22, 1, 0.36, 1],
                            delay: index * CARD_STAGGER,
                          }}
                        >
                          <ResultCard
                            result={result}
                            sourceName={sourceName(result.source_id)}
                            recordHref={recordHref}
                            recordPage={recordPage}
                          />
                        </motion.div>
                      ))}
                </AnimatePresence>
                {!running && verdict.data && shown.length === 0 && (
                  <p className="rounded-card border border-line px-5 py-6 text-body text-ink-muted">
                    {latest.length
                      ? `No record in ${plural(latest.length, 'list')}. Each list's answer is under What happened.`
                      : expected.length
                        ? `A check covers ${plural(expected.length, 'list')}.`
                        : 'Run a check to see results here.'}
                  </p>
                )}
              </motion.section>

              <motion.div layout="position" transition={FOLLOW} className="flex flex-col gap-3">
                {history.isError && <ErrorNote>{history.error.message}</ErrorNote>}
                <Timeline
                  entries={timelineEntries(history.data?.pages.flatMap((page) => page.items) ?? [])}
                  provider={provider}
                  sourceName={sourceName}
                  older={{
                    more: history.hasNextPage,
                    loading: history.isFetchingNextPage,
                    load: () => void history.fetchNextPage(),
                    retentionDays: history.data?.pages[0]?.retention_days ?? null,
                  }}
                />
              </motion.div>
            </div>
          </div>
        </LayoutGroup>

        <FactsColumn provider={provider} listed={listed} />
      </div>

      <RegistryModal provider={provider} />
      <RecordModal provider={provider} />
    </div>
  )
}

/**
 * The right column: one Facts card, the roster's facts with what NPPES records about the NPI
 * (the NPI card's lookup, already cached) and what the lists that matched the provider say
 * merged in, each value marked with its sources. The roster's rows show at once; the others
 * rise in as the lookup and the verdict answer, and a failed lookup leaves the roster's card
 * (the NPI card shows the error). The column sticks on desktop only while it is the roster's
 * facts alone: the merged card is taller than the window.
 */
function FactsColumn({
  provider,
  listed,
}: {
  provider: Provider
  listed: readonly ListedRecord[]
}) {
  const lookup = useNpi(provider.npi ?? '', provider.npi !== null)
  const registry = lookup.data?.record ?? null
  const alone = !registry && listed.length === 0
  return (
    <div
      className={`lg:row-span-2 lg:grid lg:grid-rows-subgrid ${alone ? 'lg:sticky lg:top-28 lg:self-start' : ''}`}
    >
      <FactsPanel provider={provider} registry={registry} listed={listed} />
    </div>
  )
}

/**
 * The registry over the provider's page at `/providers/:id/registry`: the whole registry page in
 * a dialog, closed with its X, Escape, the scrim or the browser's back. Opening and closing it
 * never leaves the page (the route shares the page's key). Without an NPI there is no registry
 * to show, so the route leads back to the page.
 */
function RegistryModal({ provider }: { provider: Provider }) {
  const open = useMatch('/providers/:id/registry') !== null
  const navigate = useNavigate()
  const titleId = useId()
  const close = useCallback(
    () => navigate(`/providers/${provider.id}`, { replace: true }),
    [navigate, provider.id],
  )
  if (open && !provider.npi) return <Navigate to={`/providers/${provider.id}`} replace />
  return (
    <AnimatePresence>
      {open && provider.npi && (
        <Modal labelledBy={titleId} onClose={close}>
          <NpiDetails npi={provider.npi} embedded titleId={titleId} />
        </Modal>
      )}
    </AnimatePresence>
  )
}

/**
 * An exclusion record over the provider's page at `/providers/:id/record/:recordId`, opened by a
 * result card's list name: the whole record (`RecordDetails embedded`, without "Check as
 * provider") in a dialog, closed like the registry's. A record linked from inside opens in the
 * same dialog (`recordHref`); the route shares the page's key, so nothing transitions.
 */
function RecordModal({ provider }: { provider: Provider }) {
  const recordId = useMatch('/providers/:id/record/:recordId')?.params.recordId
  const navigate = useNavigate()
  const titleId = useId()
  const close = useCallback(
    () => navigate(`/providers/${provider.id}`, { replace: true }),
    [navigate, provider.id],
  )
  const recordHref = (otherId: string) => recordPath(provider.id, otherId)
  return (
    <AnimatePresence>
      {recordId && (
        <Modal labelledBy={titleId} onClose={close} contentKey={recordId}>
          <RecordDetails
            key={recordId}
            recordId={recordId}
            embedded
            titleId={titleId}
            recordHref={recordHref}
          />
        </Modal>
      )}
    </AnimatePresence>
  )
}

/**
 * Whether a result had changed since the person last looked, as it stood when the page opened,
 * and then, once, the visit itself: `POST /seen`, so the roster stops reading "Changed" (its
 * list, counts and this status are refetched). Rendering never waits for it.
 */
function useSeenOnOpen(id: string, status: ReturnType<typeof useProviderStatus>): boolean | null {
  const [changed, setChanged] = useState<boolean | null>(null)
  const loaded = status.data && !status.isPlaceholderData ? status.data : null
  if (changed === null && (loaded || status.isError)) setChanged(loaded?.changed ?? false)
  const { mutate: markSeen } = useMarkSeen(id)
  const marked = useRef(false)
  useEffect(() => {
    if (changed === null || marked.current) return
    marked.current = true
    markSeen()
  }, [changed, markSeen])
  return changed
}

function HeaderSkeleton() {
  return (
    <div aria-label="Loading provider" className="flex flex-col gap-10 lg:gap-14">
      <div className="flex items-center gap-4 lg:mt-9">
        <Skeleton className="size-12 rounded-full lg:size-16" />
        <div className="flex flex-col gap-3">
          <Skeleton className="h-8 w-72 max-w-full lg:h-11" />
          <Skeleton className="h-4 w-40" />
        </div>
      </div>
      <Skeleton className="h-[260px] w-full rounded-hero" />
    </div>
  )
}
