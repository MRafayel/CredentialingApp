import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useState } from 'react'
import { useMatch, useNavigate } from 'react-router'
import { ButtonLink, Button } from '../../components/Button.tsx'
import { SEARCH_INPUT } from '../../components/Field.tsx'
import { Icon } from '../../components/Icon.tsx'
import { EmptyDrawer, EmptyProviders } from '@/components/illustrations'
import { stagger } from '../../components/motion.ts'
import { ErrorNote } from '../../components/Note.tsx'
import { PageBar, PageBody, PageHeader } from '../../components/PageBar.tsx'
import { TypedPlaceholder } from '../../components/TypedPlaceholder.tsx'
import { useTypedPlaceholder } from '../../components/useTypedPlaceholder.ts'
import { plural } from '../../lib/format.ts'
import { PAGE_SIZE, useProviderList } from '../../lib/queries.ts'
import { useRecedingHero } from '../../lib/scroll.ts'
import { AddProviderSheet } from './AddProviderSheet.tsx'
import { useListState } from './listState.ts'
import { Pager } from './Pager.tsx'
import { ProviderRow, ProviderRowSkeleton } from './ProviderRow.tsx'

const SEARCH_DELAY_MS = 250

/** Made-up queries the empty field types out: a name, an NPI, an organization. */
const EXAMPLES = ['Sampleperson', '1234567893', 'Sampleville Care Group'] as const

/** The roster: a card per provider. */
const LIST = 'flex flex-col gap-2.5'

export function ProvidersPage() {
  const { q, page, setQ, setPage } = useListState()
  const [draft, setDraft] = useState(q)
  const [focused, setFocused] = useState(false)
  const typed = useTypedPlaceholder(EXAMPLES, !focused && draft === '')
  const list = useProviderList(q, page)
  const adding = useMatch('/providers/new') !== null
  const navigate = useNavigate()
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

  const data = list.data
  const empty = data !== undefined && data.total === 0

  return (
    <>
      <PageBar title="Providers" />
      <PageBody>
        <motion.div style={hero.title}>
          <PageHeader
            centered
            title="Providers"
            subtitle={
              data ? (
                `${plural(data.total, 'provider')} on your roster`
              ) : (
                <span className="skeleton inline-block h-4 w-40 rounded-md align-middle" />
              )
            }
          />
        </motion.div>
        {!(empty && !q) && (
          // The tools row, receding under the nav with the header: on desktop four equal columns,
          // the search centred over the middle two (half the column, under the centred header) and
          // Add at the right end. On phone the search spans the column and the tab bar has Add. The
          // field sits 32 px (desktop 52 px) under the header and the first row 20 px under it.
          <motion.div
            ref={heroAnchor}
            style={hero.field}
            className="mt-8 mb-5 flex items-center gap-4 lg:mt-[52px] lg:grid lg:grid-cols-4 lg:gap-0"
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
            <div className="hidden shrink-0 lg:col-start-4 lg:block lg:justify-self-end">
              <ButtonLink variant="primary" icon="plus" to="/providers/new" className="h-11">
                Add provider
              </ButtonLink>
            </div>
          </motion.div>
        )}

        <motion.div style={hero.results}>
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
          ) : empty && !q ? (
            <EmptyRoster />
          ) : empty ? (
            <NoMatches query={q} />
          ) : (
            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={`${q}|${page}`}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: list.isPlaceholderData ? 0.6 : 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2 }}
              >
                <motion.ul variants={stagger()} initial="hidden" animate="shown" className={LIST}>
                  {data?.items.map((provider) => (
                    <ProviderRow key={provider.id} provider={provider} />
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
    </>
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
