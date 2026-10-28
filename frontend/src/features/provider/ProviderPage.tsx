import { AnimatePresence, motion } from 'motion/react'
import { useParams } from 'react-router'
import { ButtonLink } from '../../components/Button.tsx'
import { CopyValue } from '../../components/CopyValue.tsx'
import { ErrorNote } from '../../components/Note.tsx'
import { PageBar, PageBody, PageHeader } from '../../components/PageBar.tsx'
import { Skeleton } from '../../components/Skeleton.tsx'
import { ApiError, type Provider, type SourceStatus } from '../../lib/api.ts'
import { plural } from '../../lib/format.ts'
import { displayName } from '../../lib/provider.ts'
import { checkRuns } from '../../lib/history.ts'
import { needsAttention } from '../../lib/results.ts'
import {
  useProvider,
  useRunCheck,
  useSources,
  useVerdict,
  useVerifications,
} from '../../lib/queries.ts'
import { ProviderMark } from '../providers/ProviderMark.tsx'
import { FactsPanel } from './FactsPanel.tsx'
import { ResultCard, ResultCardSkeleton } from './ResultCard.tsx'
import { Timeline } from './Timeline.tsx'
import { VerdictHero } from './VerdictHero.tsx'

const BACK = { to: '/providers', label: 'Providers' }
const CARD_STAGGER = 0.08

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
        {provider.data ? <Loaded provider={provider.data} /> : <HeaderSkeleton />}
      </PageBody>
    </>
  )
}

function Loaded({ provider }: { provider: Provider }) {
  const verdict = useVerdict(provider.id)
  const history = useVerifications(provider.id)
  const sources = useSources()
  const run = useRunCheck(provider.id)

  const byId = new Map<string, SourceStatus>(
    (sources.data ?? []).map((source) => [source.source_id, source]),
  )
  const sourceName = (sourceId: string) => byId.get(sourceId)?.display_name ?? sourceId
  const latest = verdict.data?.results ?? []
  const shown = latest.filter(needsAttention)
  const expected = sources.data ?? []
  const running = run.isPending

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
                <span aria-hidden="true">·</span>
                <span className="inline-flex items-center gap-1">
                  NPI
                  <CopyValue value={provider.npi} label="NPI" />
                </span>
              </>
            )}
          </span>
        }
      />

      <div className="flex flex-col gap-3">
        {verdict.data ? (
          <VerdictHero
            verdict={verdict.data}
            expected={expected.length}
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

      <div className="grid gap-12 lg:grid-cols-[minmax(0,1fr)_320px] lg:gap-10">
        <div className="flex min-w-0 flex-col gap-12 lg:gap-14">
          <section
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
                      <ResultCard result={result} sourceName={sourceName(result.source_id)} />
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
          </section>

          <div className="flex flex-col gap-3">
            {history.isError && <ErrorNote>{history.error.message}</ErrorNote>}
            <Timeline
              runs={checkRuns(history.data ?? [])}
              provider={provider}
              sourceName={sourceName}
            />
          </div>
        </div>

        <div className="lg:sticky lg:top-28 lg:self-start">
          <FactsPanel provider={provider} />
        </div>
      </div>
    </div>
  )
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
