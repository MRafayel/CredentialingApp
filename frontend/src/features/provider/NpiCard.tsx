import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { Button } from '../../components/Button.tsx'
import { CopyValue } from '../../components/CopyValue.tsx'
import { Icon } from '../../components/Icon.tsx'
import { EASE, quick, settle } from '../../components/motion.ts'
import { ErrorNote } from '../../components/Note.tsx'
import { SourceLine } from '../../components/Provenance.tsx'
import { Skeleton } from '../../components/Skeleton.tsx'
import { StatusLabel } from '../../components/Status.tsx'
import { Ago, CalendarDate } from '../../components/Time.tsx'
import { TONES } from '../../components/tones.ts'
import type {
  LiveCheck,
  NpiLookup,
  NpiRecord,
  NpiSuggestions,
  Provider,
  Verification,
} from '../../lib/api.ts'
import {
  matchLevelLabel,
  npiStatusLabel,
  outcomeWord,
  registryStatusLabel,
  resultLabel,
  type Label,
} from '../../lib/labels.ts'
import { entityKindWord } from '../../lib/provider.ts'
import { useNpi, useNpiSuggestions, useSetProviderNpi, useVerifyNpi } from '../../lib/queries.ts'
import {
  evidenceWords,
  latestNpiVerification,
  registryName,
  registryPlace,
  registrySourceName,
  type EvidenceWords,
} from '../../lib/registry.ts'
import { useLookupState } from '../lookup/lookupState.ts'
import {
  candidatesLine,
  lookupDraft,
  npiShape,
  reasonWords,
  restsOnLive,
  splitSuggestions,
  type Candidate,
} from './npi.ts'
import { RegistryLink } from './RegistryLink.tsx'
import { Evidence } from './ResultCard.tsx'

const NOT_VERIFIED: Label = { tone: 'neutral', word: 'Not verified' }
const NO_NPI: Label = { tone: 'review', word: 'No NPI on file' }
const NAME_DIFFERS: Label = { tone: 'review', word: 'Name differs in NPPES' }

/** How many suggestions show before "Show all". */
const SHOWN = 5
/** The card's size follows its shape: a larger layout move. */
const MORPH = { duration: 0.36, ease: EASE }

const BODY = 'flex flex-col gap-3 p-5 sm:p-6'
const STATUS_ROW = 'flex flex-wrap items-center gap-x-3 gap-y-1 text-small'
const ACTIONS = 'flex flex-wrap items-center gap-2 pt-1'

/**
 * The provider's NPI as NPPES knows it, above the exclusion results. Three shapes, morphing
 * into each other: an NPI with its latest verification (status, source, time, "Verify live"),
 * an NPI never verified (who the registry says it is, "Verify"), and no NPI (the registry's
 * suggestions, each set only through "Use this NPI" and a confirm). Nothing is verified or set
 * without the user.
 */
export function NpiCard({ provider, history }: { provider: Provider; history: Verification[] }) {
  const shape = npiShape(provider.npi, latestNpiVerification(history))
  const titleId = useId()
  return (
    <motion.article
      layout
      transition={{ layout: MORPH }}
      style={{ borderRadius: 16 }}
      aria-labelledby={titleId}
      className="rounded-card bg-surface shadow-card"
    >
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.div
          key={shape.kind}
          layout="position"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={settle}
        >
          {shape.kind === 'verified' ? (
            <Verified
              providerId={provider.id}
              npi={shape.npi}
              result={shape.result}
              titleId={titleId}
            />
          ) : shape.kind === 'unverified' ? (
            <Unverified providerId={provider.id} npi={shape.npi} titleId={titleId} />
          ) : (
            <Missing provider={provider} titleId={titleId} />
          )}
        </motion.div>
      </AnimatePresence>
    </motion.article>
  )
}

// --- Shape 1: an NPI with its latest verification --------------------------------------------

function Verified({
  providerId,
  npi,
  result,
  titleId,
}: {
  providerId: string
  npi: string
  result: Verification
  titleId: string
}) {
  const lookup = useNpi(npi)
  const verify = useVerifyNpi(providerId)
  const [open, setOpen] = useState(false)
  const panelId = useId()
  const record = lookup.data ? lookup.data.record : undefined
  const live = restsOnLive(result.provenance.source_id)
  const status = result.details.npi_status
  // The backend's sentence where the mark and the source line do not say it all.
  const explain = result.outcome !== 'completed' || status === 'mirror_only_active'
  const liveFailed = result.details.live_check_outcome

  return (
    <>
      <div className={BODY}>
        <div aria-live="polite">
          <AnimatePresence mode="popLayout" initial={false}>
            <motion.div
              key={result.id}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={settle}
              className={STATUS_ROW}
            >
              <StatusLabel label={resultLabel(result)} chip className="text-body" />
              {status === 'deactivated' && record && <Deactivation record={record} />}
              <SourceLine block={result} kind={live ? 'live' : 'mirror'} />
              {!live && (
                <span className="text-ink-muted sm:ml-auto">
                  Verified <Ago at={result.created_at} />
                </span>
              )}
            </motion.div>
          </AnimatePresence>
        </div>
        <Title npi={npi} titleId={titleId} />
        <Identity record={record} />
        {result.details.name_match === 'high' && (
          <StatusLabel label={NAME_DIFFERS} size={14} className="text-small" />
        )}
        {explain && <p className="max-w-[72ch] text-body text-ink-muted">{result.summary}</p>}
        {liveFailed && (
          <p className="flex flex-wrap items-center gap-x-1.5 text-small font-medium text-ink">
            <BlockedMark />
            Live check: {outcomeWord(liveFailed)}
            {result.details.live_check_at && (
              <>
                <span aria-hidden="true">·</span>
                <Ago at={result.details.live_check_at} className="font-normal text-ink-muted" />
              </>
            )}
          </p>
        )}
        {verify.isError && <ErrorNote>{verify.error.message}</ErrorNote>}
        <div className={ACTIONS}>
          <RegistryLink npi={npi} className="-ml-2.5" />
          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            aria-controls={panelId}
            className="inline-flex h-8 items-center gap-1 rounded-full px-2 text-sm font-medium text-ink-muted transition-colors hover:text-ink"
          >
            {open ? 'Hide details' : 'Details'}
            <motion.span animate={{ rotate: open ? 180 : 0 }} transition={settle}>
              <Icon name="chevronDown" size={15} />
            </motion.span>
          </button>
          <Button
            variant="secondary"
            icon="bolt"
            className="ml-auto"
            disabled={verify.isPending}
            aria-busy={verify.isPending}
            onClick={() => verify.mutate({ live: true })}
          >
            {verify.isPending ? 'Checking…' : 'Verify live'}
          </Button>
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
            <Evidence result={result}>
              <a
                href={result.provenance.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex h-8 items-center gap-1 self-start rounded-full text-small font-medium text-accent-strong underline-offset-4 hover:underline"
              >
                {registrySourceName(result.provenance.source_id)}
                <Icon name="external" size={14} />
                <span className="sr-only">(opens in a new tab)</span>
              </a>
            </Evidence>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}

/** "since Mar 1, 2024 · replaced by 1234567893", in ink beside the deactivated mark. */
function Deactivation({ record }: { record: NpiRecord }) {
  const since = record.deactivation_date
  const replacement = record.replacement_npi
  if (!since && !replacement) return null
  return (
    <span className="inline-flex flex-wrap items-center gap-x-1.5 text-ink">
      {since && (
        <span>
          since <CalendarDate value={since} />
        </span>
      )}
      {since && replacement && <span aria-hidden="true">·</span>}
      {replacement && (
        <span>
          replaced by{' '}
          <Link
            to={`/npi/${replacement}`}
            className="font-mono text-accent-strong underline-offset-4 hover:underline"
          >
            {replacement}
          </Link>
        </span>
      )}
    </span>
  )
}

// --- Shape 2: an NPI never verified ----------------------------------------------------------

function Unverified({
  providerId,
  npi,
  titleId,
}: {
  providerId: string
  npi: string
  titleId: string
}) {
  const lookup = useNpi(npi)
  const verify = useVerifyNpi(providerId)
  const record = lookup.data ? lookup.data.record : undefined
  return (
    <div className={BODY}>
      <div className={STATUS_ROW}>
        <StatusLabel label={NOT_VERIFIED} chip className="text-body" />
      </div>
      <Title npi={npi} titleId={titleId} />
      <Identity record={lookup.isError ? null : record} />
      {lookup.data ? (
        <RegistryAnswer lookup={lookup.data} />
      ) : lookup.isError ? (
        <ErrorNote>{lookup.error.message}</ErrorNote>
      ) : (
        <Skeleton className="h-4 w-72 max-w-full" />
      )}
      {verify.isError && <ErrorNote>{verify.error.message}</ErrorNote>}
      <div className={ACTIONS}>
        <RegistryLink npi={npi} className="-ml-2.5" />
        <Button
          variant="secondary"
          icon="check"
          className="ml-auto"
          disabled={verify.isPending}
          aria-busy={verify.isPending}
          onClick={() => verify.mutate({ live: false })}
        >
          {verify.isPending ? 'Checking…' : 'Verify'}
        </Button>
      </div>
    </div>
  )
}

/** What the registry says now, before any verification: its status with both source lines. */
function RegistryAnswer({ lookup }: { lookup: NpiLookup }) {
  return (
    <div className="flex flex-col gap-1 text-small">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <StatusLabel label={registryStatusLabel(lookup.status)} size={14} />
        {lookup.mirror && <SourceLine block={lookup.mirror} kind="mirror" />}
      </div>
      {lookup.live && <LiveLine live={lookup.live} />}
    </div>
  )
}

/** The live block: its source line when the registry answered, its failure when it did not. */
function LiveLine({ live }: { live: LiveCheck }) {
  if (live.outcome === 'completed') return <SourceLine block={live} kind="live" />
  return (
    <span className="flex flex-wrap items-center gap-x-1.5 text-ink-muted">
      <span className="inline-flex items-center gap-1.5 font-medium text-ink">
        <BlockedMark />
        {outcomeWord(live.outcome)}
      </span>
      <span aria-hidden="true">·</span>
      <span>{registrySourceName(live.provenance.source_id)}</span>
      <span aria-hidden="true">·</span>
      <Ago at={live.provenance.verified_at} />
      {live.note && <span className="basis-full">{live.note}</span>}
    </span>
  )
}

// --- Shape 3: no NPI on file -----------------------------------------------------------------

function Missing({ provider, titleId }: { provider: Provider; titleId: string }) {
  const suggestions = useNpiSuggestions(provider.id, true)
  const setNpi = useSetProviderNpi(provider.id)
  const { setDraft } = useLookupState()
  const navigate = useNavigate()
  const data = suggestions.data
  const unloaded = data !== undefined && data.mirror === null

  function search() {
    setDraft(lookupDraft(provider))
    void navigate('/lookup')
  }

  return (
    <div className={BODY}>
      <div className={STATUS_ROW}>
        <StatusLabel label={NO_NPI} chip className="text-body" />
      </div>
      <h3 id={titleId} className="text-title">
        NPI
      </h3>
      {data ? (
        unloaded ? (
          <p className="flex items-center gap-1.5 text-body font-medium text-ink">
            <BlockedMark size={16} />
            The registry mirror is not loaded
          </p>
        ) : (
          <Suggestions
            data={data}
            pending={setNpi.isPending ? setNpi.variables : null}
            onConfirm={(npi) => setNpi.mutate(npi)}
          />
        )
      ) : suggestions.isError ? (
        <ErrorNote>{suggestions.error.message}</ErrorNote>
      ) : (
        <SuggestionsSkeleton />
      )}
      {setNpi.isError && <ErrorNote>{setNpi.error.message}</ErrorNote>}
      {!unloaded && (
        <div className={ACTIONS}>
          <Button variant="ghost" icon="search" className="-ml-3" onClick={search}>
            Search NPPES
          </Button>
        </div>
      )}
    </div>
  )
}

function Suggestions({
  data,
  pending,
  onConfirm,
}: {
  data: NpiSuggestions
  pending: string | null
  onConfirm: (npi: string) => void
}) {
  const [confirming, setConfirming] = useState<string | null>(null)
  const [all, setAll] = useState(false)
  const { candidates, reasons } = splitSuggestions(data.suggestions)

  if (!candidates.length) {
    const Mark = TONES.neutral.Mark
    return (
      <div className="flex flex-col gap-1">
        {(reasons.length
          ? reasons.map((row) => reasonWords(row.explanation))
          : ['No candidate']
        ).map((reason) => (
          <p key={reason} className="flex items-center gap-1.5 text-body font-medium text-ink">
            <Mark size={16} className={`shrink-0 ${TONES.neutral.text}`} />
            {reason}
          </p>
        ))}
        {data.mirror && <SourceLine block={data.mirror} kind="mirror" />}
      </div>
    )
  }

  const shown = all ? candidates : candidates.slice(0, SHOWN)
  return (
    <div className="flex flex-col gap-2">
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-small">
        <span className="font-medium text-ink">{candidatesLine(data.found)}</span>
        {data.mirror && <SourceLine block={data.mirror} kind="mirror" />}
      </p>
      <ul className="flex flex-col border-y border-line">
        <AnimatePresence initial={false}>
          {shown.map((candidate, index) => (
            <SuggestionRow
              key={candidate.npi}
              candidate={candidate}
              first={index === 0}
              confirming={confirming === candidate.npi}
              pending={pending === candidate.npi}
              locked={pending !== null}
              onUse={() => setConfirming(candidate.npi)}
              onCancel={() => setConfirming(null)}
              onConfirm={() => onConfirm(candidate.npi)}
            />
          ))}
        </AnimatePresence>
      </ul>
      {candidates.length > SHOWN && (
        <Button
          variant="ghost"
          className="-ml-3 h-8 self-start"
          aria-expanded={all}
          onClick={() => {
            if (
              all &&
              confirming &&
              !candidates.slice(0, SHOWN).some((c) => c.npi === confirming)
            ) {
              setConfirming(null)
            }
            setAll((value) => !value)
          }}
        >
          {all ? 'Show fewer' : `Show all ${candidates.length}`}
        </Button>
      )}
    </div>
  )
}

/**
 * One NPI the registry suggests: its match level, who the registry says it is, the evidence in
 * a few words, and "Use this NPI", which turns into the inline confirm. Focus moves to Confirm,
 * and back to "Use this NPI" on Cancel or Escape.
 */
function SuggestionRow({
  candidate,
  first,
  confirming,
  pending,
  locked,
  onUse,
  onCancel,
  onConfirm,
}: {
  candidate: Candidate
  first: boolean
  confirming: boolean
  pending: boolean
  locked: boolean
  onUse: () => void
  onCancel: () => void
  onConfirm: () => void
}) {
  const registry = candidate.registry
  const rowRef = useRef<HTMLLIElement>(null)
  const useButton = useRef<HTMLButtonElement>(null)
  const confirmButton = useRef<HTMLButtonElement>(null)
  const wasConfirming = useRef(confirming)

  useEffect(() => {
    if (confirming) {
      confirmButton.current?.focus()
    } else if (wasConfirming.current) {
      // Back to the row's action only when focus was in the confirm that just closed.
      const active = document.activeElement
      if (!active || active === document.body || rowRef.current?.contains(active)) {
        useButton.current?.focus()
      }
    }
    wasConfirming.current = confirming
  }, [confirming])

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape' && !pending) {
      event.stopPropagation()
      onCancel()
    }
  }

  const facts = registry
    ? [entityKindWord(registry.entity_kind), registry.credential, registryPlace(registry)].filter(
        (fact): fact is string => Boolean(fact),
      )
    : []

  return (
    <motion.li
      ref={rowRef}
      aria-busy={pending}
      initial={{ height: 0, opacity: 0 }}
      animate={{ height: 'auto', opacity: 1 }}
      exit={{ height: 0, opacity: 0 }}
      transition={settle}
      // The row clips while it grows; its 8 px of side room keeps the focus rings whole.
      className="-mx-2 overflow-hidden px-2"
    >
      <div
        className={`flex flex-col py-4 transition-opacity duration-180 ${first ? '' : 'border-t border-line'} ${pending ? 'opacity-60' : ''}`}
      >
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:gap-4">
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <StatusLabel
                label={matchLevelLabel(candidate.level)}
                chip
                size={14}
                className="text-small"
              />
              <Link
                to={`/npi/${candidate.npi}`}
                className="min-w-0 rounded-sm text-body font-semibold break-words text-ink underline-offset-4 hover:underline"
              >
                {registry ? (registryName(registry) ?? 'Name not published') : 'Name not published'}
              </Link>
            </div>
            <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-small text-ink-muted">
              {registry && <StatusLabel label={npiStatusLabel(registry.status)} size={13} />}
              {facts.map((fact, index) => (
                <span key={fact} className="inline-flex items-center gap-2">
                  {(index > 0 || registry) && <span aria-hidden="true">·</span>}
                  {fact}
                </span>
              ))}
            </p>
            <EvidenceLine words={evidenceWords(candidate.evidence)} />
            <CopyValue value={candidate.npi} label="NPI" className="-ml-1.5 self-start" />
          </div>
          <AnimatePresence initial={false} mode="popLayout">
            {!confirming && (
              <motion.div
                key="use"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={quick}
                className="shrink-0"
              >
                <Button
                  ref={useButton}
                  variant="secondary"
                  className="h-9"
                  disabled={locked}
                  onClick={onUse}
                >
                  Use this NPI
                </Button>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
        <AnimatePresence initial={false}>
          {confirming && (
            <motion.div
              key="confirm"
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={settle}
              className="overflow-hidden"
              onKeyDown={onKeyDown}
            >
              <div className="pt-3">
                <div className="flex flex-col gap-3 rounded-control bg-soft px-4 py-3 sm:flex-row sm:items-center">
                  <p className="min-w-0 flex-1 text-body text-ink">
                    Set NPI <span className="font-mono tabular-nums">{candidate.npi}</span> on this
                    provider?
                  </p>
                  <div className="flex items-center gap-2">
                    <Button
                      ref={confirmButton}
                      variant="primary"
                      className="h-9"
                      disabled={pending}
                      aria-busy={pending}
                      onClick={onConfirm}
                    >
                      {pending ? 'Setting…' : 'Confirm'}
                    </Button>
                    <Button variant="ghost" className="h-9" disabled={pending} onClick={onCancel}>
                      Cancel
                    </Button>
                  </div>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </motion.li>
  )
}

/** "same ZIP · first name" muted, then each field that differs with a red ✕. */
function EvidenceLine({ words }: { words: EvidenceWords }) {
  if (!words.matched.length && !words.differs.length) return null
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-small">
      {words.matched.length > 0 && (
        <span className="text-ink-muted">
          <span className="sr-only">Matched: </span>
          {words.matched.join(' · ')}
        </span>
      )}
      {words.differs.map((word) => (
        <span key={word} className="inline-flex items-baseline gap-1 text-ink">
          <span aria-hidden="true" className="font-bold text-status-listed">
            ✕
          </span>
          <span className="sr-only">Differs: </span>
          {word}
        </span>
      ))}
    </p>
  )
}

function SuggestionsSkeleton() {
  return (
    <div className="flex flex-col border-y border-line">
      <span className="sr-only">Searching the registry</span>
      {[0, 1].map((row) => (
        <div
          key={row}
          aria-hidden="true"
          className={`flex items-start gap-3 py-4 ${row ? 'border-t border-line' : ''}`}
        >
          <Skeleton className="size-7 shrink-0 rounded-full" />
          <span className="flex flex-1 flex-col gap-2 pt-1">
            <Skeleton className="h-4 w-48 max-w-full" />
            <Skeleton className="h-3 w-32" />
          </span>
          <Skeleton className="hidden h-9 w-28 rounded-full sm:block" />
        </div>
      ))}
    </div>
  )
}

// --- Shared pieces ---------------------------------------------------------------------------

/** "NPI 1234567893": the card's title with the NPI as published on the roster, copyable. */
function Title({ npi, titleId }: { npi: string; titleId: string }) {
  return (
    <div className="flex flex-wrap items-center gap-x-2">
      <h3 id={titleId} className="text-title">
        NPI
      </h3>
      <CopyValue value={npi} label="NPI" />
    </div>
  )
}

/**
 * Who the registry says the NPI is: "Sampleperson, Avery · Individual · Austin, TX". Undefined
 * while the lookup loads; null when the registry has no record (the status says why).
 */
function Identity({ record }: { record: NpiRecord | null | undefined }) {
  if (record === undefined) return <Skeleton className="h-5 w-64 max-w-full" />
  if (record === null) return null
  const place = registryPlace(record)
  const name = registryName(record)
  return (
    <p className="flex flex-wrap items-center gap-x-2 text-body text-ink-muted">
      <span className={name ? 'font-medium text-ink' : ''}>{name ?? 'Name not published'}</span>
      <span aria-hidden="true">·</span>
      <span>{entityKindWord(record.entity_kind)}</span>
      {place && (
        <>
          <span aria-hidden="true">·</span>
          <span>{place}</span>
        </>
      )}
    </p>
  )
}

function BlockedMark({ size = 14 }: { size?: number }) {
  const Mark = TONES.blocked.Mark
  return <Mark size={size} className={`shrink-0 ${TONES.blocked.text}`} />
}

/** The card's shape while the history it depends on loads. */
export function NpiCardSkeleton() {
  return (
    <div className="rounded-card bg-surface p-5 shadow-card sm:p-6">
      <div className="flex items-center gap-3">
        <Skeleton className="size-7 rounded-full" />
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-3.5 w-48" />
      </div>
      <h3 className="mt-3 text-title text-ink-muted">NPI</h3>
      <Skeleton className="mt-3 h-4 w-64 max-w-full" />
      <Skeleton className="mt-5 h-8 w-28 rounded-full" />
    </div>
  )
}
