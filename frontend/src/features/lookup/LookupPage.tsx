import {
  AnimatePresence,
  motion,
  useIsPresent,
  useReducedMotion,
  type Transition,
  type Variants,
} from 'motion/react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { Button } from '../../components/Button.tsx'
import { CopyValue } from '../../components/CopyValue.tsx'
import { SEARCH_INPUT } from '../../components/Field.tsx'
import { EmptyDrawer, VerdictBlocked } from '@/components/illustrations'
import { Icon } from '../../components/Icon.tsx'
import { EASE, quick, rise, settle, stagger } from '../../components/motion.ts'
import { ErrorNote } from '../../components/Note.tsx'
import { PageBar, PageBody, PageHeader } from '../../components/PageBar.tsx'
import { Skeleton } from '../../components/Skeleton.tsx'
import { StatusLabel } from '../../components/Status.tsx'
import { TypedPlaceholder } from '../../components/TypedPlaceholder.tsx'
import { useTypedPlaceholder } from '../../components/useTypedPlaceholder.ts'
import { CalendarDate } from '../../components/Time.tsx'
import type { Lookup, LookupRecord, SearchedSource } from '../../lib/api.ts'
import {
  actionLabel,
  draftFromRecord,
  isSearchable,
  matchesLine,
  noMatchesLine,
  notLoadedText,
  placeText,
  searchedScope,
} from '../../lib/lookup.ts'
import { displayName, type ProviderDraft } from '../../lib/provider.ts'
import { LOOKUP_LIMIT, useExclusionLookup } from '../../lib/queries.ts'
import { useRecedingHero } from '../../lib/scroll.ts'
import { AddProviderSheet } from '../providers/AddProviderSheet.tsx'
import { CARD, CARD_LINK } from './cards.ts'
import { useLookupState } from './lookupState.ts'
import { stateName } from './map/mapInfo.ts'
import { StatesMap } from './map/StatesMap.tsx'

const SEARCH_DELAY_MS = 300

/** Made-up queries the empty field types out, one of each kind the lookup accepts. */
const EXAMPLES = ['1234567893', 'Smith, John', 'Acme Pharmacy Inc', '00048309'] as const

/** Results: a card per record, so each one reads on its own. */
const LIST = 'flex flex-col gap-3'

/**
 * Identity, status, identifiers, action. Fractions after one auto column (the same button in
 * every card), so the columns line up from card to card.
 */
const COLUMNS =
  'flex flex-col gap-4 p-5 lg:grid lg:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)_minmax(0,1fr)_auto] lg:items-center lg:gap-8 lg:px-7 lg:py-6'

type View =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'error'; message: string; retry: () => void }
  | { kind: 'results'; data: Lookup; refreshing: boolean }
  | { kind: 'empty'; data: Lookup; refreshing: boolean }

/**
 * Search every exclusion list directly, open a hit to see what the lists hold around it, or
 * turn it into a tracked provider. A state chosen on the map narrows the search to it; the map
 * folds away while the field holds anything. The search and its state are kept above the
 * routes, so they are still there when the reader comes back from a record.
 */
export function LookupPage() {
  const { draft, q, state, setDraft, setQ, setState } = useLookupState()
  const [prefill, setPrefill] = useState<ProviderDraft | null>(null)
  const [focused, setFocused] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const typed = useTypedPlaceholder(EXAMPLES, !focused && draft === '')
  const typing = draft.trim() !== ''
  // An emptied field searches nothing, even before the pause that updates `q`.
  const searchable = isSearchable(q) && typing
  const lookup = useExclusionLookup(q, state, searchable)
  const { anchor: heroAnchor, ...hero } = useRecedingHero()
  const reduced = useReducedMotion() ?? false
  // The swap runs one way at a time. Typing: the map leaves, then results may show. Clearing:
  // the results leave, then the map comes back (at once when no results were on screen).
  const [mapGone, setMapGone] = useState(typing)
  const [resultsGone, setResultsGone] = useState(true)

  useEffect(() => {
    const term = draft.trim()
    if (term === q) return
    const timer = setTimeout(() => setQ(term), SEARCH_DELAY_MS)
    return () => clearTimeout(timer)
  }, [draft, q, setQ])

  const view: View =
    !searchable || !mapGone
      ? { kind: 'idle' }
      : lookup.isError
        ? { kind: 'error', message: lookup.error.message, retry: () => void lookup.refetch() }
        : lookup.data === undefined
          ? { kind: 'loading' }
          : {
              kind: lookup.data.records.length ? 'results' : 'empty',
              data: lookup.data,
              refreshing: lookup.isPlaceholderData,
            }
  const showResults = view.kind !== 'idle'
  const showMap = !typing && resultsGone
  // Whatever is on screen is no longer gone. Set during render, so the other one never mounts
  // beside it; each becomes gone again only when its exit completes.
  if (showResults && resultsGone) setResultsGone(false)
  if (showMap && mapGone) setMapGone(false)

  return (
    <>
      <PageBar title="Lookup" />
      <PageBody>
        <motion.div style={hero.title}>
          <PageHeader centered title="Look up" subtitle="Search every exclusion list at once." />
        </motion.div>
        {/* The field sits 24 px (desktop 40 px) under the header and recedes with it. */}
        <motion.div
          ref={heroAnchor}
          style={hero.field}
          className="mt-6 lg:mx-auto lg:mt-10 lg:max-w-[760px]"
        >
          <search className="block">
            <label htmlFor="lookup-search" className="sr-only">
              Search the exclusion lists by NPI, name or licence number
            </label>
            <div className="relative">
              <Icon
                name="search"
                size={20}
                className="pointer-events-none absolute top-1/2 left-5 -translate-y-1/2 text-ink-muted"
              />
              <input
                ref={input}
                id="lookup-search"
                type="search"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onFocus={() => setFocused(true)}
                onBlur={() => setFocused(false)}
                data-typing={typed.typing ? '' : undefined}
                placeholder="NPI, name or licence number"
                autoComplete="off"
                spellCheck={false}
                className={`${SEARCH_INPUT} w-full pr-14 pl-13 [&::-webkit-search-cancel-button]:appearance-none`}
              />
              {typed.typing && (
                <TypedPlaceholder text={typed.text} className="right-14 left-[53px] text-[17px]" />
              )}
              <AnimatePresence initial={false}>
                {draft && (
                  <motion.button
                    type="button"
                    aria-label="Clear search"
                    onClick={() => {
                      setDraft('')
                      input.current?.focus()
                    }}
                    initial={{ opacity: 0, scale: 0.8 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.8 }}
                    transition={{ duration: 0.15 }}
                    className="absolute top-1/2 right-3 grid size-9 -translate-y-1/2 place-items-center rounded-full text-ink-muted transition-colors hover:bg-soft hover:text-ink"
                  >
                    <Icon name="close" size={16} />
                  </motion.button>
                )}
              </AnimatePresence>
            </div>
          </search>
          {/* The state filter, under the field, so it stays in view while the map is folded. */}
          <AnimatePresence initial={false}>
            {state && (
              <motion.div
                key="state-filter"
                initial={{ height: 0, opacity: 0, y: 10 }}
                animate={{ height: 'auto', opacity: 1, y: 0, transition: reduced ? INSTANT : RISE }}
                exit={{ height: 0, opacity: 0, transition: reduced ? INSTANT : quick }}
                className="overflow-hidden"
              >
                <div className="flex justify-center px-1 pt-3 pb-1">
                  <StateFilter
                    name={stateName(state)}
                    onClear={() => {
                      setState(null)
                      input.current?.focus()
                    }}
                  />
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </motion.div>

        {/* The map under the field (16 px, desktop 24 px, then the stage's own room for a state's
            text); it leaves while the field holds anything and returns when it is empty. */}
        <AnimatePresence initial={false} onExitComplete={() => setMapGone(true)}>
          {showMap && (
            <MapSlot key="map" reduced={reduced}>
              <div className="pt-4 lg:pt-6">
                <StatesMap
                  filter={state}
                  onFilter={(code) => {
                    setState(state === code ? null : code)
                    // The map can sit below the fold: bring the title, field and chip back
                    // into view so the choice is seen, and let typing start at once.
                    window.scrollTo({ top: 0, behavior: reduced ? 'instant' : 'smooth' })
                    input.current?.focus({ preventScroll: true })
                  }}
                />
              </div>
            </MapSlot>
          )}
        </AnimatePresence>

        <motion.section
          style={hero.results}
          aria-live="polite"
          aria-busy={view.kind === 'loading'}
          aria-label="Lookup results"
          className="mt-5 lg:mt-12"
        >
          <AnimatePresence initial={false} onExitComplete={() => setResultsGone(true)}>
            {showResults && (
              <motion.div
                key="results"
                exit={{ opacity: 0, y: -6, transition: reduced ? INSTANT : quick }}
              >
                <LookupView
                  view={view}
                  query={q}
                  onCheck={(record) => setPrefill(draftFromRecord(record))}
                />
              </motion.div>
            )}
          </AnimatePresence>
        </motion.section>
      </PageBody>
      <AnimatePresence>
        {prefill && <AddProviderSheet initial={prefill} onClose={() => setPrefill(null)} />}
      </AnimatePresence>
    </>
  )
}

function LookupView({
  view,
  query,
  onCheck,
}: {
  view: View
  query: string
  onCheck: (record: LookupRecord) => void
}) {
  switch (view.kind) {
    case 'idle':
      // The subtitle already says what the page does; nothing shows until there is an answer.
      return null
    case 'loading':
      return (
        <ul aria-label="Searching the exclusion lists" className={LIST}>
          {Array.from({ length: 3 }, (_, index) => (
            <LookupRowSkeleton key={index} />
          ))}
        </ul>
      )
    case 'error':
      return (
        <ErrorNote
          action={
            <Button variant="ghost" className="-my-1 h-8" onClick={view.retry}>
              Try again
            </Button>
          }
        >
          {view.message}
        </ErrorNote>
      )
    case 'empty':
      return (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: view.refreshing ? 0.6 : 1, y: 0 }}
          transition={{ duration: 0.2 }}
          className="flex flex-col items-center gap-2 rounded-card bg-surface px-6 py-12 text-center shadow-card"
        >
          <EmptyDrawer />
          <p className="mt-4 text-title">
            {noMatchesLine(view.data.sources, view.data.state && stateName(view.data.state))} for “
            {query}”
          </p>
          <ListsLine sources={view.data.sources} state={view.data.state} className="max-w-xl" />
        </motion.div>
      )
    case 'results': {
      const { records, sources } = view.data
      const names = new Map(sources.map((source) => [source.source_id, source.display_name]))
      return (
        <motion.div
          animate={{ opacity: view.refreshing ? 0.6 : 1 }}
          transition={{ duration: 0.2 }}
          className="flex flex-col gap-4"
        >
          <div className="flex flex-col gap-1">
            <p className="text-subheading">
              {matchesLine(
                records.length,
                LOOKUP_LIMIT,
                view.data.state && stateName(view.data.state),
              )}
            </p>
            <ListsLine sources={sources} state={view.data.state} className="max-w-5xl" />
          </div>
          <motion.ul
            key={query}
            variants={stagger()}
            initial="hidden"
            animate="shown"
            className={LIST}
          >
            {records.map((record) => (
              <LookupRow
                key={record.id}
                record={record}
                sourceName={names.get(record.source_id) ?? record.source_id}
                onCheck={() => onCheck(record)}
              />
            ))}
          </motion.ul>
        </motion.div>
      )
    }
  }
}

const INSTANT: Transition = { duration: 0 }
/** The map's and the filter chip's arrival: a list row's rise, a touch longer. */
const RISE: Transition = { duration: 0.36, ease: EASE }

/**
 * The map's return. It waits 80 ms at opacity 0 first: the freshly mounted map, a large SVG under
 * a 3D tilt, is rasterized during that pause, while still invisible, so the rise itself runs on
 * a painted layer and drops no frames.
 */
const MAP_RISE: Variants = {
  hidden: { opacity: 0, y: 10 },
  shown: { opacity: 1, y: 0, transition: { ...RISE, delay: 0.08 } },
}

/**
 * The map's slot. It leaves the way the receding hero does (fading and shrinking to 0.96 in
 * place, `settle`) and comes back the way a list row arrives (`MAP_RISE`); instant under reduced
 * motion. While it leaves it is hidden from assistive technology and out of the tab order.
 */
function MapSlot({ reduced, children }: { reduced: boolean; children: ReactNode }) {
  const present = useIsPresent()
  return (
    <motion.div
      variants={MAP_RISE}
      initial={reduced ? false : 'hidden'}
      animate="shown"
      exit={{ opacity: 0, scale: 0.96, transition: reduced ? INSTANT : settle }}
      aria-hidden={present ? undefined : true}
      inert={!present}
    >
      {children}
    </motion.div>
  )
}

/** "Searching in Washington" with a way out: the state the lookup is narrowed to. */
function StateFilter({ name, onClear }: { name: string; onClear: () => void }) {
  return (
    <p className="inline-flex h-9 items-center gap-1 rounded-full border border-line-strong bg-surface pr-1 pl-4 text-small text-ink">
      <span>
        Searching in <span className="font-semibold">{name}</span>
      </span>
      <button
        type="button"
        aria-label="Clear state filter"
        onClick={onClear}
        className="grid size-7 place-items-center rounded-full text-ink-muted transition-colors hover:bg-soft hover:text-ink"
      >
        <Icon name="close" size={14} />
      </button>
    </p>
  )
}

/**
 * "Searched 9 states and the federal list": where an answer looked. Lists never loaded are
 * named as such, never counted as searched.
 */
function ListsLine({
  sources,
  state,
  className,
}: {
  sources: readonly SearchedSource[]
  /** The state the search was narrowed to, from the response. */
  state: string | null
  className?: string
}) {
  const scope = searchedScope(sources, state ? { code: state, name: stateName(state) } : null)
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
function LookupRow({
  record,
  sourceName,
  onCheck,
}: {
  record: LookupRecord
  sourceName: string
  onCheck: () => void
}) {
  const label = actionLabel(record.action)
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

function LookupRowSkeleton() {
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
