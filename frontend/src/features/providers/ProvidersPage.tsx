import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useState } from 'react'
import { useLocation, useMatch, useNavigate, useSearchParams } from 'react-router'
import type { RosterOrder } from '../../lib/api.ts'
import { ButtonLink, Button } from '../../components/Button.tsx'
import { SEARCH_INPUT } from '../../components/Field.tsx'
import { Icon } from '../../components/Icon.tsx'
import { EmptyDrawer, EmptyProviders, ScanningDocument } from '@/components/illustrations'
import { stagger } from '../../components/motion.ts'
import { ErrorNote } from '../../components/Note.tsx'
import { TONES } from '../../components/tones.ts'
import { PageBar, PageBody, PageHeader } from '../../components/PageBar.tsx'
import { TypedPlaceholder } from '../../components/TypedPlaceholder.tsx'
import { useTypedPlaceholder } from '../../components/useTypedPlaceholder.ts'
import { addedLine, importedLine } from '../../lib/importing.ts'
import { attentionWord } from '../../lib/labels.ts'
import {
  filterAttention,
  filterChips,
  filterKind,
  parseFilter,
  parseKind,
  parseOrder,
  rosterLine,
  toggled,
  type RosterFilter,
  type RosterKind,
} from '../../lib/monitoring.ts'
import { PAGE_SIZE, useProviderList, useProviderSummary, useSourceName } from '../../lib/queries.ts'
import { useRecedingHero } from '../../lib/scroll.ts'
import { AddProviderSheet } from './AddProviderSheet.tsx'
import { FilterChips } from './FilterChips.tsx'
import { FilterMenu } from './FilterMenu.tsx'
import { ImportSheet } from './ImportSheet.tsx'
import { useListState } from './listState.ts'
import { Pager } from './Pager.tsx'
import { ProviderRow, ProviderRowSkeleton } from './ProviderRow.tsx'

const SEARCH_DELAY_MS = 250

/** Made-up queries the empty field types out: a name, an NPI, an organization. */
const EXAMPLES = ['Sampleperson', '1234567893', 'Sampleville Care Group'] as const

/** The roster: a card per provider. */
const LIST = 'flex flex-col gap-2.5'

/**
 * The monitored roster: who needs a person first. The header counts them, the chips keep one
 * reason (kept in the URL as `?attention=`, a reason and never a name; everyone by default, those
 * needing a person first), and each row says its one reason in a word and a line. The backend
 * decides and orders; the page only names.
 */
export function ProvidersPage() {
  const { q, page, setQ, setPage } = useListState()
  const [draft, setDraft] = useState(q)
  const [focused, setFocused] = useState(false)
  const typed = useTypedPlaceholder(EXAMPLES, !focused && draft === '')
  const [params, setParams] = useSearchParams()
  const filter = parseFilter(params.get('attention'))
  const kind = parseKind(params.get('kind'))
  const order = parseOrder(params.get('order'))
  const list = useProviderList(q, page, {
    attention: filterAttention(filter),
    kind: filterKind(kind),
    order: order === 'attention' ? undefined : order,
  })
  const summary = useProviderSummary()
  const sourceName = useSourceName()
  const adding = useMatch('/providers/new') !== null
  const importing = useMatch('/providers/import') !== null
  const navigate = useNavigate()
  const location = useLocation()
  const imported = importedNote(location.state)
  const { anchor: heroAnchor, ...hero } = useRecedingHero()

  useEffect(() => {
    const term = draft.trim()
    if (term === q) return
    const timer = setTimeout(() => {
      setQ(term)
      setPage(1)
    }, SEARCH_DELAY_MS)
    return () => clearTimeout(timer)
  }, [draft, q, setQ, setPage])

  /** The URL names only the choices away from their defaults, a reason and never a name. */
  function choose(next: { filter?: RosterFilter; kind?: RosterKind; order?: RosterOrder }) {
    const chosen = { filter, kind, order, ...next }
    if (chosen.filter === filter && chosen.kind === kind && chosen.order === order) return
    setPage(1)
    setParams(
      {
        ...(chosen.filter === 'all' ? {} : { attention: chosen.filter }),
        ...(chosen.kind === 'all' ? {} : { kind: chosen.kind }),
        ...(chosen.order === 'attention' ? {} : { order: chosen.order }),
      },
      { replace: true },
    )
  }

  const data = list.data
  const counts = summary.data
  // Nobody on the roster at all: the first-use state, whatever the filter says.
  const noRoster = counts?.total === 0 && !q
  const empty = data !== undefined && data.total === 0
  const line = counts ? rosterLine(counts) : null

  return (
    <>
      <PageBar title="Providers" />
      <PageBody>
        <motion.div style={hero.title}>
          <PageHeader
            centered
            title="Providers"
            subtitle={
              line ? (
                <span aria-live="polite">
                  <span className="font-medium text-ink">{line.lead}</span>
                  <span> · {line.total}</span>
                </span>
              ) : (
                <span className="skeleton inline-block h-4 w-40 rounded-md align-middle" />
              )
            }
          />
        </motion.div>
        {!noRoster && (
          // The tools row, receding under the nav with the header: on desktop four equal columns,
          // the search centred over the middle two (half the column, under the centred header).
          // On phone the search spans the column. The field sits 32 px (desktop 52 px) under the
          // header and the first row 20 px under it. The row's scroll transform makes it a
          // stacking context, as the results' does: raised above the results so the filter
          // menu hangs over the rows, under the fixed top bar (z-30).
          <motion.div
            ref={heroAnchor}
            style={hero.field}
            className="relative z-20 mt-8 mb-5 flex items-center gap-3 lg:mt-[52px] lg:grid lg:grid-cols-4 lg:gap-0"
          >
            <search className="relative block w-full min-w-0 flex-1 lg:col-span-2 lg:col-start-2">
              <Icon
                name="search"
                size={20}
                className="pointer-events-none absolute top-1/2 left-5 -translate-y-1/2 text-ink-muted"
              />
              <label htmlFor="provider-search" className="sr-only">
                Search your providers
              </label>
              <input
                id="provider-search"
                type="search"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onFocus={() => setFocused(true)}
                onBlur={() => setFocused(false)}
                data-typing={typed.typing ? '' : undefined}
                placeholder="Search your providers"
                autoComplete="off"
                spellCheck={false}
                className={`${SEARCH_INPUT} w-full pr-5 pl-13`}
              />
              {typed.typing && (
                <TypedPlaceholder text={typed.text} className="right-5 left-[53px] text-[17px]" />
              )}
            </search>
            {/* The other filters (kind, order) beside the search: they apply to every chip. */}
            <div className="lg:col-start-4 lg:pl-3">
              <FilterMenu
                kind={kind}
                order={order}
                onKind={(next) => choose({ kind: next })}
                onOrder={(next) => choose({ order: next })}
              />
            </div>
          </motion.div>
        )}

        <motion.div style={hero.results}>
          <AnimatePresence initial={false}>
            {imported && (
              <ImportedNote
                note={imported}
                onDismiss={() =>
                  navigate({ search: location.search }, { replace: true, state: null })
                }
              />
            )}
          </AnimatePresence>
          {counts && !noRoster && (
            // The chips centred under the search, Add provider at the right end of the same row
            // on desktop (owner, 2026-11-12); on phone the chips scroll alone and the tab bar
            // has Add.
            <div className="mb-5 lg:grid lg:grid-cols-[1fr_auto_1fr] lg:items-center lg:gap-4">
              <div className="min-w-0 lg:col-start-2">
                <FilterChips
                  chips={filterChips(counts, filter)}
                  current={filter}
                  onChange={(next) => choose({ filter: toggled(filter, next, 'all') })}
                />
              </div>
              <div className="hidden lg:col-start-3 lg:flex lg:justify-self-end">
                <ButtonLink variant="primary" icon="plus" to="/providers/new" className="h-11">
                  Add provider
                </ButtonLink>
              </div>
            </div>
          )}
          {list.isError && !data ? (
            <ErrorNote
              action={
                <Button variant="ghost" className="-my-1 h-8" onClick={() => void list.refetch()}>
                  Try again
                </Button>
              }
            >
              {list.error.message}
            </ErrorNote>
          ) : list.isPending ? (
            <ul aria-label="Loading providers" className={LIST}>
              {Array.from({ length: 5 }, (_, index) => (
                <ProviderRowSkeleton key={index} />
              ))}
            </ul>
          ) : noRoster || (empty && !q && filter === 'all' && kind === 'all') ? (
            <EmptyRoster />
          ) : empty && q ? (
            <NoMatches query={q} />
          ) : empty ? (
            <NobodyHere filter={filter} kind={kind} />
          ) : (
            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={`${q}|${page}|${filter}|${kind}|${order}`}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: list.isPlaceholderData ? 0.6 : 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2 }}
              >
                <motion.ul variants={stagger()} initial="hidden" animate="shown" className={LIST}>
                  {data?.items.map((provider) => (
                    <ProviderRow key={provider.id} provider={provider} sourceName={sourceName} />
                  ))}
                </motion.ul>
                {data && (
                  <Pager page={page} pageSize={PAGE_SIZE} total={data.total} onPage={setPage} />
                )}
              </motion.div>
            </AnimatePresence>
          )}
        </motion.div>
      </PageBody>
      <AnimatePresence>
        {adding && <AddProviderSheet onClose={() => navigate('/providers')} />}
      </AnimatePresence>
      <AnimatePresence>
        {importing && (
          <ImportSheet
            onClose={() => navigate('/providers')}
            onImported={(report) => {
              setPage(1)
              void navigate('/providers', { state: { imported: importedLine(report) } })
            }}
          />
        )}
      </AnimatePresence>
    </>
  )
}

type ImportedNoteData = ReturnType<typeof importedLine>

/** The note an import leaves in the page's history state, if this visit carries one. */
function importedNote(state: unknown): ImportedNoteData | null {
  if (typeof state !== 'object' || state === null || !('imported' in state)) return null
  const note = state.imported
  if (typeof note !== 'object' || note === null || !('added' in note)) return null
  return note as ImportedNoteData
}

/**
 * "120 of 2,000 providers added" while the first check runs, a scanning document beside it,
 * following the roster's summary (read again while a check runs) until nobody is waiting; then
 * "2,000 providers added" with the clear mark.
 */
function ImportedNote({ note, onDismiss }: { note: ImportedNoteData; onDismiss: () => void }) {
  const Done = TONES.clear.Mark
  const waiting = Math.min(useProviderSummary().data?.checking ?? note.checking, note.checking)
  const checking = waiting > 0
  return (
    <motion.div
      initial={{ opacity: 0, y: -6, height: 0 }}
      animate={{ opacity: 1, y: 0, height: 'auto' }}
      exit={{ opacity: 0, height: 0 }}
      transition={{ duration: 0.26, ease: [0.22, 1, 0.36, 1] }}
      className="overflow-hidden"
    >
      <div className="mx-auto mb-5 flex max-w-2xl items-start gap-3 rounded-card border border-line bg-surface py-2 pr-2 pl-4 text-body">
        <span className="relative mt-[7px] size-5 shrink-0">
          <AnimatePresence initial={false}>
            {checking ? (
              <motion.span
                key="scanning"
                exit={{ opacity: 0, scale: 0.85 }}
                transition={{ duration: 0.2 }}
                className="absolute inset-0 text-ink-muted"
              >
                <ScanningDocument size={20} />
              </motion.span>
            ) : (
              <motion.span
                key="done"
                initial={{ opacity: 0, scale: 0.85 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ duration: 0.2 }}
                className={`absolute inset-0 ${TONES.clear.text}`}
              >
                <Done size={20} />
              </motion.span>
            )}
          </AnimatePresence>
        </span>
        <output className="block min-w-0 flex-1 py-1.5 font-medium tabular-nums">
          {addedLine(note.added, waiting)}
        </output>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss"
          className="grid size-9 shrink-0 place-items-center rounded-full text-ink-muted transition-colors hover:bg-soft hover:text-ink"
        >
          <Icon name="close" size={18} />
        </button>
      </div>
    </motion.div>
  )
}

function EmptyRoster() {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="mx-auto mt-16 flex max-w-sm flex-col items-center gap-5 text-center"
    >
      <EmptyProviders size={116} />
      <div>
        <h2 className="text-subheading font-semibold">No providers yet</h2>
        <p className="mt-1 text-body text-ink-muted">Add one to run its first check.</p>
      </div>
      <ButtonLink variant="primary" icon="plus" to="/providers/new">
        Add provider
      </ButtonLink>
    </motion.div>
  )
}

/**
 * A reason with nobody left under it, for instance after the last one was opened, or a kind
 * the roster has none of.
 */
function NobodyHere({ filter, kind }: { filter: RosterFilter; kind: RosterKind }) {
  const word = filter === 'all' ? null : attentionWord(filter).word.toLowerCase()
  const who =
    kind === 'individual' ? 'individuals' : kind === 'organization' ? 'organizations' : null
  return (
    <motion.p
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className="mx-auto mt-10 max-w-sm text-center text-title"
    >
      {word && who
        ? `No ${who} marked ${word}`
        : word
          ? `No providers marked ${word}`
          : who
            ? `No ${who} on the roster`
            : 'Nothing needs you'}
    </motion.p>
  )
}

/** A search of the roster that found nobody. */
function NoMatches({ query }: { query: string }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className="mx-auto mt-10 flex max-w-sm flex-col items-center gap-4 text-center"
    >
      <EmptyDrawer />
      <p className="text-title break-words">No providers match “{query}”</p>
    </motion.div>
  )
}
