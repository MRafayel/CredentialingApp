import { AnimatePresence, motion } from 'motion/react'
import { useId, useState, type FormEvent, type ReactNode } from 'react'
import { OrganizationMark, PersonMark, VerdictReview } from '@/components/illustrations'
import { Button } from '../../components/Button.tsx'
import { Field } from '../../components/Field.tsx'
import { Icon } from '../../components/Icon.tsx'
import { glide, settle, stagger } from '../../components/motion.ts'
import { ErrorNote } from '../../components/Note.tsx'
import { SourceLine } from '../../components/Provenance.tsx'
import { Sheet } from '../../components/Sheet.tsx'
import { ApiError, type NpiSearchQuery } from '../../lib/api.ts'
import { plural } from '../../lib/format.ts'
import { missing, stateError, useFocusInvalid, without, type FormErrors } from '../../lib/forms.ts'
import { outcomeWord } from '../../lib/labels.ts'
import { useNpiSearch } from '../../lib/queries.ts'
import { liveDraft, liveQuery, type LiveDraft } from './npiSearch.ts'
import { RegistryRow } from './RegistryParts.tsx'

/** How many results one live search asks the registry for. */
const LIVE_LIMIT = 50

type Kind = LiveDraft['kind']

const stateCode = (value: string) =>
  value
    .toUpperCase()
    .replace(/[^A-Z]/g, '')
    .slice(0, 2)

/**
 * One structured request to the live NPPES registry, prefilled from the lookup's query. The
 * answer lists the registry's NPIs in its order with where and when it answered; a request the
 * registry did not answer says why, never "no results"; a query it would refuse says so under
 * the fields.
 */
export function NpiSearchSheet({ query, onClose }: { query: string; onClose: () => void }) {
  const formId = useId()
  const [draft, setDraft] = useState<LiveDraft>(() => liveDraft(query))
  const [submitted, setSubmitted] = useState<NpiSearchQuery | null>(null)
  const search = useNpiSearch(submitted ?? {}, submitted !== null)
  // The form's own messages (the browser's bubble is off): checked on submit, cleared as typed.
  const [errors, setErrors] = useState<FormErrors>({})
  useFocusInvalid(formId, errors)
  const change = (patch: Partial<LiveDraft>) => {
    setDraft((old) => ({ ...old, ...patch }))
    setErrors((old) => Object.keys(patch).reduce(without, old))
  }
  const searching = submitted !== null && search.isFetching
  const refused =
    search.error instanceof ApiError && search.error.status === 422 ? search.error.message : null

  function submit(event: FormEvent) {
    event.preventDefault()
    const found: FormErrors =
      draft.kind === 'individual'
        ? missing({ last: draft.last })
        : missing({ organization: draft.organization })
    const state = stateError(draft.state)
    if (state) found.state = state
    setErrors(found)
    if (Object.keys(found).length > 0) return
    const next = liveQuery(draft, LIVE_LIMIT)
    // The same question again is asked again: a live search never answers from the cache.
    if (submitted && JSON.stringify(next) === JSON.stringify(submitted)) void search.refetch()
    else setSubmitted(next)
  }

  return (
    <Sheet
      title="Search NPPES"
      onClose={onClose}
      footer={
        <Button
          type="submit"
          form={formId}
          variant="primary"
          icon="search"
          className="h-12 w-full text-body"
          disabled={searching}
          aria-busy={searching}
        >
          {searching ? 'Searching…' : 'Search'}
        </Button>
      }
    >
      <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-6 pt-2">
        <fieldset>
          <legend className="mb-3 text-label">Kind</legend>
          <div className="grid grid-cols-2 gap-3">
            <KindTile
              value="individual"
              current={draft.kind}
              onChange={(kind) => change({ kind })}
              title="Individual"
              icon={<PersonMark size={24} />}
            />
            <KindTile
              value="organization"
              current={draft.kind}
              onChange={(kind) => change({ kind })}
              title="Organization"
              icon={<OrganizationMark size={24} />}
            />
          </div>
        </fieldset>

        <AnimatePresence mode="popLayout" initial={false}>
          <motion.div
            key={draft.kind}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={settle}
            className="grid grid-cols-2 gap-3"
          >
            {draft.kind === 'individual' ? (
              <>
                <Field
                  label="Last name"
                  value={draft.last}
                  onChange={(event) => change({ last: event.target.value })}
                  warning={errors.last}
                  autoComplete="off"
                  className="col-span-2 sm:col-span-1"
                />
                <Field
                  label="First name"
                  value={draft.first}
                  onChange={(event) => change({ first: event.target.value })}
                  autoComplete="off"
                  className="col-span-2 sm:col-span-1"
                />
              </>
            ) : (
              <Field
                label="Organization name"
                value={draft.organization}
                onChange={(event) => change({ organization: event.target.value })}
                warning={errors.organization}
                autoComplete="off"
                className="col-span-2"
              />
            )}
          </motion.div>
        </AnimatePresence>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-[96px_minmax(0,1fr)]">
          <Field
            label="State"
            value={draft.state}
            onChange={(event) => change({ state: stateCode(event.target.value) })}
            warning={errors.state}
            maxLength={2}
            autoComplete="off"
            inputClassName="font-mono uppercase"
            className="col-span-2 sm:col-span-1"
          />
          <Field
            label="City"
            value={draft.city}
            onChange={(event) => change({ city: event.target.value })}
            autoComplete="off"
            className="col-span-2 sm:col-span-1"
          />
        </div>

        <AnimatePresence initial={false}>
          {refused && (
            <motion.p
              key="refused"
              role="alert"
              initial={{ opacity: 0, y: -4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={settle}
              className="-mt-2 flex items-start gap-1.5 text-small font-medium text-ink"
            >
              <VerdictReview size={14} className="mt-0.5 shrink-0" />
              {refused}
            </motion.p>
          )}
        </AnimatePresence>
      </form>

      <section aria-label="Live results" aria-live="polite" aria-busy={searching} className="mt-8">
        <LiveResults search={search} refused={refused !== null} />
      </section>
    </Sheet>
  )
}

function LiveResults({
  search,
  refused,
}: {
  search: ReturnType<typeof useNpiSearch>
  refused: boolean
}) {
  if (refused) return null
  if (search.isError) return <ErrorNote>{search.error.message}</ErrorNote>
  const data = search.data
  if (!data) {
    return search.isFetching ? (
      <p className="text-small text-ink-muted">Asking the NPPES registry…</p>
    ) : null
  }
  const live = data.live
  if (live && live.outcome !== 'completed') {
    return (
      <ErrorNote>
        <span className="font-semibold">{outcomeWord(live.outcome)}</span>
        {live.note ? `: ${live.note}` : ''}
      </ErrorNote>
    )
  }
  const count = data.records.length
  return (
    <motion.div
      animate={{ opacity: search.isPlaceholderData || search.isFetching ? 0.6 : 1 }}
      transition={{ duration: 0.2 }}
      className="flex flex-col gap-4"
    >
      <p className="flex flex-wrap items-center gap-x-1.5 text-small">
        <span className="font-semibold text-ink tabular-nums">
          {count ? plural(count, 'result') : 'No results'}
        </span>
        {live && (
          <>
            <span aria-hidden="true" className="text-ink-muted">
              ·
            </span>
            <SourceLine block={live} kind="live" />
          </>
        )}
      </p>
      {count > 0 && (
        <motion.ul
          key={data.live?.provenance.verified_at ?? 'results'}
          variants={stagger()}
          initial="hidden"
          animate="shown"
          className="flex flex-col gap-3"
        >
          {data.records.map((record) => (
            <RegistryRow key={record.npi} record={record} />
          ))}
        </motion.ul>
      )}
      {count >= LIVE_LIMIT && (
        <p className="text-small text-ink-muted">
          First {count}. Add a state or city to narrow it.
        </p>
      )}
    </motion.div>
  )
}

/** The kind of party to search for, as a tile like the add-provider form's. */
function KindTile({
  value,
  current,
  onChange,
  title,
  icon,
}: {
  value: Kind
  current: Kind
  onChange: (kind: Kind) => void
  title: string
  icon: ReactNode
}) {
  const selected = value === current
  return (
    <label
      className={`relative flex cursor-pointer flex-col items-start gap-3 rounded-card border bg-surface p-4 transition-colors has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-accent ${selected ? 'border-ink' : 'border-line-strong hover:border-ink-faint'}`}
    >
      <input
        type="radio"
        name="npi_search_kind"
        value={value}
        checked={selected}
        onChange={() => onChange(value)}
        data-autofocus={selected ? '' : undefined}
        className="sr-only"
      />
      <span className="text-ink">{icon}</span>
      <span className="text-body font-semibold">{title}</span>
      {selected && (
        <motion.span
          layoutId="npi-kind-tick"
          transition={glide}
          className="absolute top-3 right-3 grid size-5 place-items-center rounded-full bg-accent text-on-ink"
        >
          <Icon name="check" size={13} />
        </motion.span>
      )}
    </label>
  )
}
