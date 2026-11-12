import { AnimatePresence, motion } from 'motion/react'
import { useId, useState, type FormEvent, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { Button, ButtonLink } from '../../components/Button.tsx'
import { Field, FieldMessage, INPUT } from '../../components/Field.tsx'
import { Icon } from '../../components/Icon.tsx'
import { OrganizationMark, PersonMark } from '@/components/illustrations'
import { glide, settle } from '../../components/motion.ts'
import { ErrorNote } from '../../components/Note.tsx'
import { Sheet } from '../../components/Sheet.tsx'
import type { ProviderCreate, RosterRole } from '../../lib/api.ts'
import {
  missing,
  npiError,
  stateError,
  useFocusInvalid,
  without,
  type FormErrors,
} from '../../lib/forms.ts'
import { roleWord } from '../../lib/labels.ts'
import { npiState } from '../../lib/npi.ts'
import type { ProviderDraft } from '../../lib/provider.ts'
import { useAddProvider } from '../../lib/queries.ts'

type Kind = 'individual' | 'organization'
type LicenceRow = { key: number; number: string; state: string }
type AddressRow = { key: number; city: string; state: string }

let nextKey = 1
const newKey = () => nextKey++

const stateCode = (value: string) =>
  value
    .toUpperCase()
    .replace(/[^A-Z]/g, '')
    .slice(0, 2)
const blankToNull = (value: string) => value.trim() || null

const ROLES: readonly RosterRole[] = ['practitioner', 'workforce', 'vendor']
/** An organization is most often a vendor; a person a practitioner (an NPI is expected). */
const defaultRole = (kind: Kind): RosterRole =>
  kind === 'organization' ? 'vendor' : 'practitioner'

/** The form; `initial` prefills it, for example from an exclusion lookup. */
export function AddProviderSheet({
  onClose,
  initial,
}: {
  onClose: () => void
  initial?: ProviderDraft
}) {
  const navigate = useNavigate()
  const add = useAddProvider()
  const formId = useId()
  const [kind, setKind] = useState<Kind>(initial?.kind ?? 'individual')
  // The role follows the kind until the user picks one.
  const [chosenRole, setChosenRole] = useState<RosterRole | null>(null)
  const role = chosenRole ?? defaultRole(kind)
  const [last, setLast] = useState(initial?.last ?? '')
  const [first, setFirst] = useState(initial?.first ?? '')
  const [middle, setMiddle] = useState(initial?.middle ?? '')
  const [suffix, setSuffix] = useState(initial?.suffix ?? '')
  const [organization, setOrganization] = useState(initial?.organization ?? '')
  const [npi, setNpi] = useState(initial?.npi ?? '')
  const [dateOfBirth, setDateOfBirth] = useState('')
  const [licences, setLicences] = useState<LicenceRow[]>(() =>
    initial?.licences.length
      ? initial.licences.map((licence) => ({ key: newKey(), ...licence }))
      : [{ key: newKey(), number: '', state: '' }],
  )
  // One empty row to start, as the licences have: nothing to click before typing a city or state.
  const [addresses, setAddresses] = useState<AddressRow[]>(() => [
    { key: newKey(), city: '', state: '' },
  ])
  // The form's own messages (the browser's bubble is off): checked on submit, cleared as typed.
  const [errors, setErrors] = useState<FormErrors>({})
  useFocusInvalid(formId, errors)
  const clear = (key: string) => setErrors((old) => without(old, key))

  const npiNow = npiState(npi)

  /** What the form refuses to send, by field: blank names, a wrong NPI shape, half a licence. */
  function check(): FormErrors {
    const found: FormErrors =
      kind === 'individual' ? missing({ first, last }) : missing({ organization })
    const npiMessage = npiError(npi)
    if (npiMessage) found.npi = npiMessage
    for (const row of licences) {
      const state = stateError(row.state)
      // A state alone is a known licence state (owner, 2026-11-12); a number alone is not.
      if (state) found[`licence-${row.key}`] = state
      else if (row.number.trim() && !row.state) found[`licence-${row.key}`] = 'State needed'
    }
    for (const row of addresses) {
      const state = stateError(row.state)
      if (state) found[`address-${row.key}`] = state
    }
    return found
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    const found = check()
    setErrors(found)
    if (Object.keys(found).length > 0) return
    const body: ProviderCreate = {
      entity_kind: kind,
      role,
      ...(kind === 'individual'
        ? {
            person_name: {
              last: last.trim(),
              first: first.trim(),
              middle: blankToNull(middle),
              suffix: blankToNull(suffix),
            },
          }
        : { organization_name: organization.trim() }),
      npi: blankToNull(npi),
      date_of_birth: kind === 'individual' ? blankToNull(dateOfBirth) : null,
      licenses: licences
        .filter((row) => row.number.trim() || row.state)
        .map((row) => ({ number: blankToNull(row.number), state: row.state })),
      addresses: addresses
        .filter((row) => row.city.trim() || row.state)
        .map((row) => ({ city: blankToNull(row.city), state: row.state || null })),
    }
    add.mutate(body, {
      onSuccess: (provider) => navigate(`/providers/${provider.id}`),
    })
  }

  return (
    <Sheet
      title="Add provider"
      onClose={onClose}
      footer={
        <Button
          type="submit"
          form={formId}
          variant="primary"
          icon="plus"
          className="h-12 w-full text-body"
          disabled={add.isPending}
        >
          {add.isPending ? 'Adding and checking…' : 'Add provider'}
        </Button>
      }
    >
      <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-7 pt-2">
        <p className="-mt-1 flex items-center justify-between gap-3 text-small text-ink-muted">
          Many at once?
          <ButtonLink variant="ghost" to="/providers/import" replace className="-mr-2 h-8 px-2">
            Import a list
          </ButtonLink>
        </p>
        <AnimatePresence initial={false}>
          {add.isError && (
            <motion.div
              initial={{ opacity: 0, y: -6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={settle}
            >
              <ErrorNote>{add.error.message}</ErrorNote>
            </motion.div>
          )}
        </AnimatePresence>

        <fieldset>
          <legend className="mb-3 text-label">Kind</legend>
          <div className="grid grid-cols-2 gap-3">
            <KindCard
              value="individual"
              current={kind}
              onChange={setKind}
              title="Individual"
              icon={<PersonMark size={24} />}
            />
            <KindCard
              value="organization"
              current={kind}
              onChange={setKind}
              title="Organization"
              icon={<OrganizationMark size={24} />}
            />
          </div>
        </fieldset>

        <fieldset>
          <legend className="mb-3 text-label">Role</legend>
          <RolePicker value={role} onChange={setChosenRole} />
        </fieldset>

        <AnimatePresence mode="popLayout" initial={false}>
          <motion.div
            key={kind}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={settle}
            className="grid grid-cols-2 gap-3"
          >
            {kind === 'individual' ? (
              <>
                <Field
                  label="First name"
                  value={first}
                  onChange={(e) => {
                    setFirst(e.target.value)
                    clear('first')
                  }}
                  warning={errors.first}
                  autoComplete="off"
                  className="col-span-2 sm:col-span-1"
                />
                <Field
                  label="Last name"
                  value={last}
                  onChange={(e) => {
                    setLast(e.target.value)
                    clear('last')
                  }}
                  warning={errors.last}
                  autoComplete="off"
                  className="col-span-2 sm:col-span-1"
                />
                <Field
                  label="Middle"
                  value={middle}
                  onChange={(e) => setMiddle(e.target.value)}
                  autoComplete="off"
                />
                <Field
                  label="Suffix"
                  value={suffix}
                  onChange={(e) => setSuffix(e.target.value)}
                  placeholder="Jr, MD"
                  autoComplete="off"
                />
              </>
            ) : (
              <Field
                label="Organization name"
                value={organization}
                onChange={(e) => {
                  setOrganization(e.target.value)
                  clear('organization')
                }}
                warning={errors.organization}
                autoComplete="off"
                className="col-span-2"
              />
            )}
          </motion.div>
        </AnimatePresence>

        <div className="grid grid-cols-2 gap-3">
          <Field
            label="NPI"
            value={npi}
            onChange={(e) => {
              setNpi(e.target.value.replace(/\D/g, '').slice(0, 10))
              clear('npi')
            }}
            inputMode="numeric"
            placeholder="10 digits"
            autoComplete="off"
            inputClassName="font-mono tabular-nums"
            className={kind === 'individual' ? 'col-span-2 sm:col-span-1' : 'col-span-2'}
            hint={npiNow === 'valid' ? 'Check digit matches' : undefined}
            warning={
              errors.npi ?? (npiNow === 'mismatch' ? "Check digit doesn't match" : undefined)
            }
          />
          {kind === 'individual' && (
            <Field
              label="Date of birth"
              type="date"
              value={dateOfBirth}
              onChange={(e) => setDateOfBirth(e.target.value)}
              hint="Used only to confirm identity matches"
              className="col-span-2 sm:col-span-1"
            />
          )}
        </div>

        <Repeater
          title="Licences"
          addLabel="Add licence"
          onAdd={() => setLicences((rows) => [...rows, { key: newKey(), number: '', state: '' }])}
        >
          {licences.map((row, index) => (
            <RepeatRow
              key={row.key}
              onRemove={() => {
                setLicences((rows) => rows.filter((r) => r.key !== row.key))
                clear(`licence-${row.key}`)
              }}
              removeLabel={`Remove licence ${index + 1}`}
              warning={errors[`licence-${row.key}`]}
            >
              <input
                aria-label={`Licence ${index + 1} number`}
                placeholder="Number"
                value={row.number}
                onChange={(e) => {
                  setLicences((rows) =>
                    rows.map((r) => (r.key === row.key ? { ...r, number: e.target.value } : r)),
                  )
                  clear(`licence-${row.key}`)
                }}
                autoComplete="off"
                className={`${INPUT} flex-1 font-mono`}
              />
              <StateInput
                label={`Licence ${index + 1} state`}
                value={row.state}
                invalid={errors[`licence-${row.key}`] !== undefined}
                onChange={(state) => {
                  setLicences((rows) => rows.map((r) => (r.key === row.key ? { ...r, state } : r)))
                  clear(`licence-${row.key}`)
                }}
              />
            </RepeatRow>
          ))}
        </Repeater>

        <Repeater
          title="Addresses"
          addLabel="Add address"
          onAdd={() => setAddresses((rows) => [...rows, { key: newKey(), city: '', state: '' }])}
        >
          {addresses.map((row, index) => (
            <RepeatRow
              key={row.key}
              onRemove={() => {
                setAddresses((rows) => rows.filter((r) => r.key !== row.key))
                clear(`address-${row.key}`)
              }}
              removeLabel={`Remove address ${index + 1}`}
              warning={errors[`address-${row.key}`]}
            >
              <input
                aria-label={`Address ${index + 1} city`}
                placeholder="City"
                value={row.city}
                onChange={(e) =>
                  setAddresses((rows) =>
                    rows.map((r) => (r.key === row.key ? { ...r, city: e.target.value } : r)),
                  )
                }
                autoComplete="off"
                className={`${INPUT} flex-1`}
              />
              <StateInput
                label={`Address ${index + 1} state`}
                value={row.state}
                invalid={errors[`address-${row.key}`] !== undefined}
                onChange={(state) => {
                  setAddresses((rows) => rows.map((r) => (r.key === row.key ? { ...r, state } : r)))
                  clear(`address-${row.key}`)
                }}
              />
            </RepeatRow>
          ))}
        </Repeater>
      </form>
    </Sheet>
  )
}

function KindCard({
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
        name="entity_kind"
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
          layoutId="kind-tick"
          transition={glide}
          className="absolute top-3 right-3 grid size-5 place-items-center rounded-full bg-accent text-on-ink"
        >
          <Icon name="check" size={13} />
        </motion.span>
      )}
    </label>
  )
}

/**
 * Practitioner, Workforce or Vendor as one segmented pill with a gliding ink indicator, the way
 * the top bar shows its sections. Native radios, so the arrow keys move the choice.
 */
function RolePicker({
  value,
  onChange,
}: {
  value: RosterRole
  onChange: (role: RosterRole) => void
}) {
  return (
    <div className="grid grid-cols-3 gap-0.5 rounded-full bg-soft p-1">
      {ROLES.map((role) => {
        const selected = role === value
        return (
          <label
            key={role}
            className={`relative flex h-9 cursor-pointer items-center justify-center rounded-full px-3 text-sm font-medium transition-colors duration-200 has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-accent ${selected ? 'text-on-ink' : 'text-ink hover:bg-surface'}`}
          >
            <input
              type="radio"
              name="role"
              value={role}
              checked={selected}
              onChange={() => onChange(role)}
              className="sr-only"
            />
            {selected && (
              <motion.span
                layoutId="role-active"
                transition={glide}
                aria-hidden="true"
                className="absolute inset-0 rounded-full bg-ink"
              />
            )}
            <span className="relative">{roleWord(role)}</span>
          </label>
        )
      })}
    </div>
  )
}

function Repeater({
  title,
  addLabel,
  onAdd,
  children,
}: {
  title: string
  addLabel: string
  onAdd: () => void
  children: ReactNode
}) {
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-3 text-label">{title}</legend>
      <AnimatePresence initial={false}>{children}</AnimatePresence>
      <Button variant="ghost" icon="plus" onClick={onAdd} className="-ml-1 self-start px-3">
        {addLabel}
      </Button>
    </fieldset>
  )
}

/** One row of a repeater: its inputs, the remove button, and its warning line when it has one. */
function RepeatRow({
  children,
  onRemove,
  removeLabel,
  warning,
}: {
  children: ReactNode
  onRemove: () => void
  removeLabel: string
  warning?: string
}) {
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, x: 12 }}
      transition={settle}
      className="flex flex-col gap-1.5"
    >
      <div className="flex items-center gap-2">
        {children}
        <button
          type="button"
          onClick={onRemove}
          aria-label={removeLabel}
          className="grid size-11 shrink-0 place-items-center rounded-full text-ink-muted transition-colors hover:bg-soft hover:text-status-listed"
        >
          <Icon name="trash" />
        </button>
      </div>
      {warning && <FieldMessage warning>{warning}</FieldMessage>}
    </motion.div>
  )
}

function StateInput({
  label,
  value,
  onChange,
  invalid = false,
}: {
  label: string
  value: string
  onChange: (state: string) => void
  invalid?: boolean
}) {
  return (
    <input
      aria-label={label}
      placeholder="State"
      value={value}
      onChange={(e) => onChange(stateCode(e.target.value))}
      maxLength={2}
      aria-invalid={invalid ? true : undefined}
      autoComplete="off"
      className={`${INPUT} w-20 shrink-0 text-center font-mono uppercase ${invalid ? 'border-status-review' : ''}`}
    />
  )
}
