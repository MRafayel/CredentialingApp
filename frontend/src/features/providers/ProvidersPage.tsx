import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useState } from 'react'
import { useMatch, useNavigate } from 'react-router'
import { ButtonLink, Button } from '../../components/Button.tsx'
import { PILL_INPUT } from '../../components/Field.tsx'
import { Icon } from '../../components/Icon.tsx'
import { EmptyProviders } from '@/components/illustrations'
import { stagger } from '../../components/motion.ts'
import { ErrorNote } from '../../components/Note.tsx'
import { PageBar, PageBody, PageHeader } from '../../components/PageBar.tsx'
import { TypedPlaceholder } from '../../components/TypedPlaceholder.tsx'
import { useTypedPlaceholder } from '../../components/useTypedPlaceholder.ts'
import { plural } from '../../lib/format.ts'
import { PAGE_SIZE, useProviderList } from '../../lib/queries.ts'
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
        {!(empty && !q) && (
          // The tools row: on desktop four equal columns, the search centred over the middle two
          // (half the column, under the centred header) and Add at the right end. On phone the
          // search is full width and the tab bar has Add.
          <div className="mt-8 mb-5 flex items-center gap-4 lg:mt-12 lg:grid lg:grid-cols-4 lg:gap-0">
            <search className="relative block w-full min-w-0 flex-1 lg:col-span-2 lg:col-start-2">
              <Icon
                name="search"
                size={17}
                className="pointer-events-none absolute top-1/2 left-4 -translate-y-1/2 text-ink-muted"
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
                className={`${PILL_INPUT} w-full pl-11`}
              />
              {typed.typing && (
                <TypedPlaceholder text={typed.text} className="right-4 left-[45px] text-body" />
              )}
            </search>
            <div className="hidden shrink-0 lg:col-start-4 lg:block lg:justify-self-end">
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
        ) : empty && !q ? (
          <EmptyRoster />
        ) : empty ? (
          <p className="rounded-card border border-line px-6 py-10 text-center text-body text-ink-muted">
            No providers match “{q}”.
          </p>
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
