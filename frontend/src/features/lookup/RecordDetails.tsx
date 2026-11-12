import { AnimatePresence, motion } from 'motion/react'
import { useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { Button, ButtonLink } from '../../components/Button.tsx'
import { CopyValue } from '../../components/CopyValue.tsx'
import { Icon, type IconName } from '../../components/Icon.tsx'
import { rise, settle, stagger } from '../../components/motion.ts'
import { ErrorNote } from '../../components/Note.tsx'
import { PageHeader } from '../../components/PageBar.tsx'
import { Skeleton } from '../../components/Skeleton.tsx'
import { CodePill, Freshness, StatusLabel } from '../../components/Status.tsx'
import { Ago, CalendarDate, DateOf } from '../../components/Time.tsx'
import { TONES } from '../../components/tones.ts'
import { useNow } from '../../components/useNow.ts'
import { ApiError, type LinkedRecord, type RecordProfile } from '../../lib/api.ts'
import { freshnessLabel, recordStatusLabel } from '../../lib/labels.ts'
import { actionLabel, draftFromRecord, placeText } from '../../lib/lookup.ts'
import { displayName, entityKindWord, type ProviderDraft } from '../../lib/provider.ts'
import { useRecordProfile } from '../../lib/queries.ts'
import { linkCaption, recordSteps, standsAlone, type RecordStepKind } from '../../lib/record.ts'
import { Fact } from '../provider/FactsPanel.tsx'
import { TimelineStep } from '../provider/Timeline.tsx'
import { HeroFigure } from '../provider/VerdictHero.tsx'
import { AddProviderSheet } from '../providers/AddProviderSheet.tsx'
import { ProviderMark } from '../providers/ProviderMark.tsx'
import { useFrom, type Back } from '../shell/back.ts'
import { CARD, CARD_LINK } from './cards.ts'
import { NpiFactValue, NpiTrouble } from './RecordNpi.tsx'

const BACK: Back = { to: '/lookup', label: 'Lookup' }
/** In a dialog the title keeps clear of its X, which sits over the top right corner. */
const CLEAR_OF_CLOSE = 'pr-12'

/** Where a record opens: its own page in Lookup. */
const lookupRecordHref = (recordId: string) => `/lookup/${recordId}`

/** Builds the link of every record the details point to. */
type RecordHref = (recordId: string) => string

const STEP_ICONS: Record<RecordStepKind, IconName> = {
  seen: 'plus',
  changed: 'refresh',
  left: 'close',
  back: 'plus',
  rewritten: 'copy',
}

const MUTED = <span className="text-ink-muted">Not published</span>
/** A loaded list whose source gives no date of its own: never the fetch time in its place. */
const NOT_STATED = <span className="text-ink-muted">Not stated</span>

/**
 * One exclusion record and what the stored lists hold around it: its status first, then its
 * history in its list, other records of the same party, the parties and NPIs named on its row,
 * and records that only share the name. Most records have little beyond themselves; every
 * section shows only when it has something to say. `embedded` is the record over a provider's
 * page: no back link, the title names the dialog (`titleId`), and no "Check as provider", since
 * the person is already looking at the provider. `recordHref` builds the link of every other
 * record, so one opened inside the dialog opens in the same dialog. On the page, "Check as
 * provider" is left out too when the person came from a provider's page (`back`). Links to other
 * pages lead back to the page around them (`useFrom`): the record's page, or, in the dialog, the
 * provider's page.
 */
export function RecordDetails({
  recordId,
  embedded = false,
  titleId,
  recordHref = lookupRecordHref,
  back = BACK,
}: {
  recordId: string
  embedded?: boolean
  titleId?: string
  recordHref?: RecordHref
  /** Where the page's Back leads (the Lookup unless the opener said otherwise). */
  back?: Back
}) {
  const profile = useRecordProfile(recordId)
  if (profile.isError && !profile.data) {
    const missing = profile.error instanceof ApiError && profile.error.status === 404
    const title = missing ? 'Not found' : 'Record'
    return (
      <div className={`flex flex-col items-start gap-6 ${embedded ? CLEAR_OF_CLOSE : ''}`}>
        <PageHeader title={title} back={embedded ? undefined : back} titleId={titleId} />
        <ErrorNote
          action={
            !missing && (
              <Button variant="ghost" className="-my-1 h-8" onClick={() => void profile.refetch()}>
                Try again
              </Button>
            )
          }
        >
          {profile.error.message}
        </ErrorNote>
        {!embedded && (
          <ButtonLink to={back.to} state={back.state} icon="arrowLeft">
            Back to {back.label}
          </ButtonLink>
        )}
      </div>
    )
  }
  return profile.data ? (
    <Loaded
      profile={profile.data}
      embedded={embedded}
      titleId={titleId}
      recordHref={recordHref}
      back={back}
    />
  ) : (
    <RecordSkeleton embedded={embedded} titleId={titleId} />
  )
}

function Loaded({
  profile,
  embedded,
  titleId,
  recordHref,
  back,
}: {
  profile: RecordProfile
  embedded: boolean
  titleId: string | undefined
  recordHref: RecordHref
  back: Back
}) {
  const [prefill, setPrefill] = useState<ProviderDraft | null>(null)
  const { record } = profile
  const kind = entityKindWord(record.entity_kind)
  // Opened from a provider's page, the record is already that provider's (owner, 2026-11-12).
  const checkable = !embedded && !back.to.startsWith('/providers/')

  return (
    <>
      <div className="flex flex-col gap-10 lg:gap-14">
        <div className={embedded ? CLEAR_OF_CLOSE : undefined}>
          <PageHeader
            back={embedded ? undefined : back}
            titleId={titleId}
            lead={<ProviderMark provider={record} size="lg" />}
            title={displayName(record)}
            subtitle={`${kind} · ${record.source_name}`}
          />
        </div>
        <div className="flex flex-col gap-3">
          <StatusHero
            profile={profile}
            onCheck={checkable ? () => setPrefill(draftFromRecord(record)) : null}
          />
          {profile.rewritten_by && <RewrittenNote href={recordHref(profile.rewritten_by)} />}
          <NpiTrouble profile={profile} />
        </div>
        <div className="grid gap-12 lg:grid-cols-[minmax(0,1fr)_320px] lg:gap-10">
          <div className="flex min-w-0 flex-col gap-12 lg:gap-14">
            <History profile={profile} />
            {profile.same_party.length > 0 && (
              <Section
                id="also-listed"
                title="Also listed"
                hint="The same NPI, or the same licence number and state, in another record."
              >
                <LinkedList records={profile.same_party} recordHref={recordHref} />
              </Section>
            )}
            <Related profile={profile} recordHref={recordHref} />
            {profile.same_name.length > 0 && (
              <Section
                id="same-name"
                title="Same name"
                hint="Only the name is shared, so these can be other people or businesses. Compare the NPI and licence before treating one as the same provider."
              >
                <LinkedList records={profile.same_name} recordHref={recordHref} />
              </Section>
            )}
            {standsAlone(profile) && (
              <p className="max-w-[75ch] text-body text-ink-muted">
                Nothing else in the stored lists points to this record.
              </p>
            )}
          </div>
          {/* A column taller than the dialog cannot stick inside it; on the page it can. */}
          <div className={embedded ? '' : 'lg:sticky lg:top-28 lg:self-start'}>
            <RecordFacts profile={profile} />
          </div>
        </div>
      </div>
      {checkable && (
        <AnimatePresence>
          {prefill && <AddProviderSheet initial={prefill} onClose={() => setPrefill(null)} />}
        </AnimatePresence>
      )}
    </>
  )
}

/**
 * The answer first: the record's status as the backend gave it, on its tinted mark, with the
 * action, the list it comes from, its dates, and how current the stored copy is. Without
 * `onCheck` (over or from a provider's page) there is no "Check as provider".
 */
function StatusHero({
  profile,
  onCheck,
}: {
  profile: RecordProfile
  onCheck: (() => void) | null
}) {
  const { record, source } = profile
  const label = recordStatusLabel(record.status)
  const tone = TONES[label.tone]
  return (
    <section aria-label="Status" className="rounded-hero bg-surface p-6 shadow-card sm:p-8 lg:p-10">
      <div className="flex flex-col gap-6 sm:flex-row sm:items-center">
        <div className="flex min-w-0 flex-1 items-center gap-5">
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
            <p className="mt-1 text-body break-words text-ink-muted">
              {actionLabel(record.action).word} · {source.display_name}
            </p>
            {record.action_raw && (
              <p className="mt-0.5 text-small break-words text-ink-muted">
                As published: {record.action_raw}
              </p>
            )}
          </div>
        </div>
        {onCheck && (
          <Button
            variant="primary"
            icon="plus"
            spinIconOnHover
            onClick={onCheck}
            className="h-11 w-full sm:w-auto sm:self-center"
          >
            Check as provider
          </Button>
        )}
      </div>
      <dl className="mt-6 grid divide-y divide-line border-t border-line sm:mt-8 sm:grid-cols-3 sm:divide-x sm:divide-y-0 sm:pt-6 lg:mt-10">
        <HeroFigure term="Effective" first>
          {record.effective_date ? <CalendarDate value={record.effective_date} /> : '—'}
        </HeroFigure>
        <HeroFigure term="End date">
          {record.end_date ? <CalendarDate value={record.end_date} /> : 'None'}
        </HeroFigure>
        <HeroFigure term="List last updated">
          {source.list_last_updated ? (
            <DateOf at={source.list_last_updated} />
          ) : source.fetched_at ? (
            'Not stated'
          ) : (
            '—'
          )}
        </HeroFigure>
      </dl>
      <p className="mt-5 flex flex-wrap items-center gap-x-1.5 text-small text-ink-muted">
        {source.freshness && <Freshness label={freshnessLabel(source.freshness)} />}
        {source.fetched_at ? (
          <span>
            {source.freshness && '· '}fetched <Ago at={source.fetched_at} />
          </span>
        ) : (
          <span>This list has not been loaded</span>
        )}
      </p>
    </section>
  )
}

/** A quiet pointer from a row the list rewrote to the record that replaced it. */
function RewrittenNote({ href }: { href: string }) {
  const from = useFrom()
  return (
    <p className="flex flex-wrap items-center gap-x-1.5 px-2 text-small text-ink-muted">
      <Icon name="copy" size={13} className="text-ink-faint" />
      <Link
        to={href}
        state={from}
        className="inline-flex items-center gap-1 rounded-full font-medium text-accent-strong underline-offset-4 hover:underline"
      >
        This row was rewritten by the list
        <Icon name="chevronRight" size={13} />
      </Link>
    </p>
  )
}

/** "In this list": what the list has said about the record since it was first fetched. */
function History({ profile }: { profile: RecordProfile }) {
  const now = useNow()
  const steps = recordSteps(profile.versions)
  return (
    <section aria-labelledby="history-heading">
      <h2 id="history-heading" className="text-subheading">
        In this list
      </h2>
      <p className="mt-1 max-w-[75ch] text-small text-ink-muted">
        What {profile.source.display_name} has said about this record since we first fetched it.
      </p>
      <motion.ol variants={stagger()} initial="hidden" animate="shown" className="mt-5">
        {steps.map((step, index) => (
          <TimelineStep
            key={step.key}
            at={step.at}
            now={now}
            icon={<Icon name={STEP_ICONS[step.kind]} size={13} />}
            title={step.title}
            detail={step.detail ?? undefined}
            last={index === steps.length - 1}
          />
        ))}
      </motion.ol>
    </section>
  )
}

/** The other parties and NPIs on the record's row, and the records they lead to. */
function Related({ profile, recordHref }: { profile: RecordProfile; recordHref: RecordHref }) {
  const { record, related } = profile
  const { related_parties: parties, related_npis: npis } = record
  if (!parties.length && !npis.length && !related.length) return null
  const listedOnItsOwn = new Set(
    related.filter((item) => item.link === 'listed_as').map((item) => item.via_npi),
  )
  return (
    <Section
      id="related"
      title="Related"
      hint="Other parties and NPIs named on the same row. The row does not say which NPI belongs to which name."
    >
      {(parties.length > 0 || npis.length > 0) && (
        <dl className="divide-y divide-line rounded-card bg-surface px-5 py-1 shadow-card">
          {parties.length > 0 && (
            <Fact term="Named on this row">
              <ul className="flex flex-col gap-1">
                {parties.map((party) => (
                  <li key={party}>{party}</li>
                ))}
              </ul>
            </Fact>
          )}
          {npis.length > 0 && (
            <Fact term="Other NPIs on this row">
              <ul className="flex flex-col gap-1">
                {npis.map((npi) => (
                  <li key={npi} className="flex flex-wrap items-center gap-x-2">
                    <CopyValue value={npi} label="NPI" className="-ml-1.5" />
                    <span className="text-small text-ink-muted">
                      {listedOnItsOwn.has(npi)
                        ? 'Listed on its own, below'
                        : 'Not listed on its own'}
                    </span>
                  </li>
                ))}
              </ul>
            </Fact>
          )}
        </dl>
      )}
      {related.length > 0 && <LinkedList records={related} recordHref={recordHref} />}
    </Section>
  )
}

function Section({
  id,
  title,
  hint,
  children,
}: {
  id: string
  title: string
  hint: string
  children: ReactNode
}) {
  return (
    <section aria-labelledby={`${id}-heading`}>
      <h2 id={`${id}-heading`} className="text-subheading">
        {title}
      </h2>
      <p className="mt-1 max-w-[75ch] text-small text-ink-muted">{hint}</p>
      <div className="mt-4 flex flex-col gap-4">{children}</div>
    </section>
  )
}

function LinkedList({
  records,
  recordHref,
}: {
  records: readonly LinkedRecord[]
  recordHref: RecordHref
}) {
  return (
    <motion.ul
      variants={stagger()}
      initial="hidden"
      animate="shown"
      className="flex flex-col gap-3"
    >
      {records.map((record) => (
        <LinkedCard key={record.id} record={record} href={recordHref(record.id)} />
      ))}
    </motion.ul>
  )
}

/**
 * Another record as a card that opens it: its list, its name and why it is tied to this one,
 * then its own status with the action and the date it took effect.
 */
function LinkedCard({ record, href }: { record: LinkedRecord; href: string }) {
  const from = useFrom()
  const place = placeText(record)
  return (
    <motion.li variants={rise} className={`group ${CARD}`}>
      <div className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center sm:gap-6">
        <div className="min-w-0 flex-1">
          <p className="truncate text-[11px] leading-4 font-semibold tracking-[0.06em] text-ink-muted uppercase">
            {record.source_name}
          </p>
          <h3 className="mt-1 text-title break-words text-ink">
            <Link to={href} state={from} className={CARD_LINK}>
              {displayName(record)}
            </Link>
          </h3>
          <p className="mt-0.5 text-small break-words text-ink-muted">
            {linkCaption(record)}
            {place && ` · ${place}`}
          </p>
        </div>
        <div className="flex flex-col gap-0.5 sm:w-60 sm:shrink-0">
          <StatusLabel label={recordStatusLabel(record.status)} size={15} chip />
          <span className="pl-9 text-small text-ink-muted tabular-nums">
            {actionLabel(record.action).word}
            {record.effective_date && (
              <>
                {' · '}
                <CalendarDate value={record.effective_date} />
              </>
            )}
          </span>
        </div>
        <Icon
          name="chevronRight"
          size={16}
          className="hidden shrink-0 text-ink-faint transition-[transform,color] group-hover:translate-x-0.5 group-hover:text-ink sm:block"
        />
      </div>
    </motion.li>
  )
}

/** What the record says about the party, its identifiers and where it comes from. */
function RecordFacts({ profile }: { profile: RecordProfile }) {
  const { record, source } = profile
  const name = record.person_name
  const place = placeText(record)
  return (
    <section aria-labelledby="facts-heading">
      <h2 id="facts-heading" className="text-subheading">
        Facts
      </h2>
      <dl className="mt-4 divide-y divide-line rounded-card bg-surface px-5 py-1 shadow-card">
        <Fact term="Kind">{entityKindWord(record.entity_kind)}</Fact>
        {name ? (
          <Fact term="Name">
            <span className="font-semibold">{name.last}</span>
            {name.first && `, ${name.first}`}
            {name.middle && ` ${name.middle}`}
            {name.suffix && ` ${name.suffix}`}
          </Fact>
        ) : (
          <Fact term="Organization">{record.organization_name ?? MUTED}</Fact>
        )}
        {record.aliases.length > 0 && (
          <Fact term="Also known as">
            <ul className="flex flex-col gap-1">
              {record.aliases.map((alias) => (
                <li key={alias}>{alias}</li>
              ))}
            </ul>
          </Fact>
        )}
        <Fact term="NPI">
          <NpiFactValue profile={profile} />
        </Fact>
        {record.related_npis.length > 0 && (
          <Fact term="Other NPIs on this row">
            <ul className="flex flex-col">
              {record.related_npis.map((npi) => (
                <li key={npi}>
                  <CopyValue value={npi} label="NPI" className="-ml-1.5" />
                </li>
              ))}
            </ul>
          </Fact>
        )}
        {record.state_provider_id && (
          <Fact term="State provider ID">
            <CopyValue
              value={record.state_provider_id}
              label="State provider ID"
              className="-ml-1.5"
            />
          </Fact>
        )}
        {record.license && (
          <Fact term="Licence">
            <span className="flex flex-wrap items-center gap-2">
              <CodePill>{record.license.state}</CodePill>
              <span className="font-mono text-small">{record.license.number ?? '—'}</span>
              {record.license.kind && <span className="text-ink-muted">{record.license.kind}</span>}
            </span>
          </Fact>
        )}
        {place && <Fact term="Place">{place}</Fact>}
        {record.raw_provider_type && <Fact term="Provider type">{record.raw_provider_type}</Fact>}
        {record.reason && <Fact term="Reason">{record.reason}</Fact>}
        <Fact term="Source">
          <span className="flex flex-col">
            <span>{source.display_name}</span>
            {source.source_url && (
              <a
                href={source.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-0.5 inline-flex items-center gap-1 self-start rounded-full text-small font-medium text-accent-strong underline-offset-4 hover:underline"
              >
                Official page
                <Icon name="external" size={13} />
                <span className="sr-only">(opens in a new tab)</span>
              </a>
            )}
          </span>
        </Fact>
        <Fact term="List last updated">
          {source.list_last_updated ? (
            <DateOf at={source.list_last_updated} />
          ) : source.fetched_at ? (
            NOT_STATED
          ) : (
            MUTED
          )}
        </Fact>
        <Fact term="Fetched">
          {source.fetched_at ? (
            <span className="flex flex-wrap items-center gap-x-1.5">
              {source.freshness && <Freshness label={freshnessLabel(source.freshness)} />}
              <DateOf at={source.fetched_at} />
            </span>
          ) : (
            <span className="text-ink-muted">Not loaded</span>
          )}
        </Fact>
        <Fact term="First seen">
          <DateOf at={profile.first_seen} />
        </Fact>
      </dl>
    </section>
  )
}

function RecordSkeleton({ embedded, titleId }: { embedded: boolean; titleId: string | undefined }) {
  return (
    <div aria-label="Loading record" className="flex flex-col gap-10 lg:gap-14">
      {/* A dialog is named by its title even before the record arrives. */}
      {titleId && (
        <h2 id={titleId} className="sr-only">
          Exclusion record
        </h2>
      )}
      {/* On the page the back link sits above the title; a dialog has none. */}
      <div className={`flex items-center gap-4 ${embedded ? '' : 'lg:mt-9'}`}>
        <Skeleton className="size-12 rounded-full lg:size-16" />
        <div className="flex flex-col gap-3">
          <Skeleton className="h-8 w-72 max-w-full lg:h-11" />
          <Skeleton className="h-4 w-40" />
        </div>
      </div>
      <Skeleton className="h-[260px] w-full rounded-hero" />
      <div className="grid gap-12 lg:grid-cols-[minmax(0,1fr)_320px] lg:gap-10">
        <div className="flex flex-col gap-4">
          <Skeleton className="h-6 w-36" />
          <Skeleton className="h-4 w-80 max-w-full" />
          <Skeleton className="h-4 w-64 max-w-full" />
        </div>
        <Skeleton className="h-96 w-full rounded-card" />
      </div>
    </div>
  )
}
