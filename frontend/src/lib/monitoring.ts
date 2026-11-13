/**
 * The monitored roster in words: the header's "3 need you", the filters with their counts, and
 * a provider's three steps (identity, screening, monitoring). Every status comes from the
 * backend; these only name it and put it in order.
 */
import type {
  Attention,
  AttentionFilter,
  KindFilter,
  ProviderStatus,
  ProviderVerdict,
  RosterOrder,
  StatusSummary,
} from './api.ts'
import { date, plural, type FormatOptions } from './format.ts'
import { ATTENTION_ORDER, attentionWord, identityLabel, type Label, type Tone } from './labels.ts'

/** How many providers have a reason for attention: every count but `none`. */
export function needingYou(summary: Pick<StatusSummary, 'counts'>): number {
  return ATTENTION_ORDER.reduce((sum, attention) => sum + (summary.counts[attention] ?? 0), 0)
}

/** The roster's header line: "3 need you" or "Nothing needs you", and the total beside it. */
export function rosterLine(summary: Pick<StatusSummary, 'counts' | 'total'>): {
  lead: string
  total: string
} {
  const count = needingYou(summary)
  const lead =
    count === 0
      ? 'Nothing needs you'
      : `${new Intl.NumberFormat().format(count)} ${count === 1 ? 'needs' : 'need'} you`
  return { lead, total: plural(summary.total, 'provider') }
}

/**
 * The roster's filter: everyone (the default; the backend puts those needing a person first) or
 * one reason. No combined "needs you" filter: each reason is its own list (owner, 2026-11-12).
 */
export type RosterFilter = 'all' | Attention

/** The filter named in the URL (`?attention=`); anything unknown is everyone. */
export function parseFilter(value: string | null): RosterFilter {
  return (ATTENTION_ORDER as readonly string[]).includes(value ?? '') ? (value as Attention) : 'all'
}

/** The `attention` the list asks the API for: the reason, or none for "All". */
export function filterAttention(filter: RosterFilter): AttentionFilter | undefined {
  return filter === 'all' ? undefined : filter
}

export type FilterChip = { filter: RosterFilter; word: string; label: Label | null; count: number }

/**
 * "All" with the roster's total, then one chip per reason that has providers (most serious
 * first). The chip in use always shows, even at 0.
 */
export function filterChips(
  summary: Pick<StatusSummary, 'counts' | 'total'>,
  current: RosterFilter,
): FilterChip[] {
  const reasons = ATTENTION_ORDER.filter(
    (attention) => (summary.counts[attention] ?? 0) > 0 || attention === current,
  ).map((attention) => {
    const label = attentionWord(attention)
    return { filter: attention, word: label.word, label, count: summary.counts[attention] ?? 0 }
  })
  return [{ filter: 'all', word: 'All', label: null, count: summary.total }, ...reasons]
}

// --- The filter menu: kind and order, over whichever chip is in use ---------------------------

/** Who the roster shows: everyone (the default), only individuals or only organizations. */
export type RosterKind = 'all' | KindFilter

export type Choice<T extends string> = { value: T; word: string }

/**
 * The menu lists only choices away from the defaults (everyone; needs you first): a choice
 * pressed again clears it, so the defaults need no line (owner, 2026-11-12).
 */
export const KIND_CHOICES: readonly Choice<KindFilter>[] = [
  { value: 'individual', word: 'Individuals' },
  { value: 'organization', word: 'Organizations' },
]

export const ORDER_CHOICES: readonly Choice<Exclude<RosterOrder, 'attention'>>[] = [
  { value: 'newest', word: 'Newest first' },
  { value: 'oldest', word: 'Oldest first' },
]

/** The choice after a press: the pressed one, or the default when it was already in use. */
export function toggled<T extends string>(current: T, pressed: T, fallback: T): T {
  return pressed === current ? fallback : pressed
}

/** The kind named in the URL (`?kind=`); anything unknown is everyone. */
export function parseKind(value: string | null): RosterKind {
  return KIND_CHOICES.some((choice) => choice.value === value) ? (value as KindFilter) : 'all'
}

/** The order named in the URL (`?order=`); anything unknown is the backend's default. */
export function parseOrder(value: string | null): RosterOrder {
  return ORDER_CHOICES.some((choice) => choice.value === value)
    ? (value as RosterOrder)
    : 'attention'
}

/** The `kind` the list asks the API for: none for everyone. */
export function filterKind(kind: RosterKind): KindFilter | undefined {
  return kind === 'all' ? undefined : kind
}

/** How many of the menu's choices are not their default: the count on the Filter pill. */
export function choicesInUse(kind: RosterKind, order: RosterOrder): number {
  return (kind === 'all' ? 0 : 1) + (order === 'attention' ? 0 : 1)
}

// --- A provider's steps ---------------------------------------------------------------------

export type StepKey = 'identity' | 'screening' | 'monitoring'
/**
 * `done` (a tick in the clear tone), `trouble` (the status's own mark), `todo` (waiting on its
 * action or, for monitoring, on its due recheck: a ringed number) and `waiting` (not reached
 * yet, grey).
 */
export type StepState = 'done' | 'trouble' | 'todo' | 'waiting'
/** A step's own action; every one is the NPI card's. Running a check is the verdict hero's. */
export type StepAction = 'find_npi' | 'verify_npi' | 'review_npi'

export type Step = {
  key: StepKey
  title: string
  line: string
  state: StepState
  tone: Tone
  action: StepAction | null
}

export const STEP_ACTIONS: Record<StepAction, string> = {
  find_npi: 'Find NPI',
  verify_npi: 'Verify NPI',
  review_npi: 'Review NPI',
}

const line = (label: Label) => (label.detail ? `${label.word} · ${label.detail}` : label.word)

function identityStep(identity: ProviderStatus['identity']): Step {
  const label = identityLabel(identity)
  const step = { key: 'identity', title: 'Identity', line: line(label), tone: label.tone } as const
  switch (identity) {
    case 'npi_active':
    case 'not_expected':
      return { ...step, state: 'done', tone: 'clear', action: null }
    case 'npi_unverified':
      return { ...step, state: 'todo', action: 'verify_npi' }
    case 'npi_missing':
      return { ...step, state: 'todo', action: 'find_npi' }
    case 'npi_deactivated':
    case 'npi_uncertain':
      return { ...step, state: 'trouble', action: 'review_npi' }
  }
}

type VerdictFigures = Pick<
  ProviderVerdict,
  'verdict' | 'sources_checked' | 'listed_in' | 'flagged_in' | 'review_in' | 'unchecked'
>

/**
 * The screening step only reads the verdict: "Run check" and "Check again" are the hero's. A
 * first check under way (`checking`) waits: its results land on their own.
 */
function screeningStep(verdict: VerdictFigures, checking: boolean): Step {
  const step = { key: 'screening', title: 'Screening', action: null } as const
  if (checking && verdict.verdict === 'not_checked') {
    return { ...step, state: 'waiting', tone: 'neutral', line: 'Checking every list now' }
  }
  switch (verdict.verdict) {
    case 'no_records':
      return {
        ...step,
        state: 'done',
        tone: 'clear',
        line: `No records in ${plural(verdict.sources_checked, 'list')}`,
      }
    case 'listed':
      return {
        ...step,
        state: 'trouble',
        tone: 'listed',
        line: `Listed in ${plural(verdict.listed_in.length, 'list')}`,
      }
    case 'flagged':
      return {
        ...step,
        state: 'trouble',
        tone: 'flagged',
        line: `Flagged in ${plural(verdict.flagged_in.length, 'list')}`,
      }
    case 'needs_review':
      return {
        ...step,
        state: 'trouble',
        tone: 'review',
        line: `Review a match in ${plural(verdict.review_in.length, 'list')}`,
      }
    case 'could_not_check':
      return {
        ...step,
        state: 'trouble',
        tone: 'blocked',
        line: `Could not check ${plural(verdict.unchecked.length, 'list')}`,
      }
    case 'not_checked':
      return { ...step, state: 'todo', tone: 'neutral', line: 'Not checked' }
  }
}

/**
 * Monitoring starts with the first screening (an NPI verification alone does not start it). A
 * recheck whose date has passed, or that the backend calls due now (no date after a check),
 * waits for the automatic run: never a tick beside "due now". The interval is the backend's.
 */
function monitoringStep(
  status: Pick<ProviderStatus, 'last_checked_at' | 'next_check_at'>,
  verdict: VerdictFigures,
  timing: StepTiming,
): Step {
  const step = { key: 'monitoring', title: 'Monitoring', action: null } as const
  if (!status.last_checked_at || verdict.verdict === 'not_checked') {
    return { ...step, state: 'waiting', tone: 'neutral', line: 'Starts after the first check' }
  }
  const next = status.next_check_at
  if (!next || Date.parse(next) <= timing.now.getTime()) {
    return { ...step, state: 'todo', tone: 'neutral', line: 'Due now · rechecked automatically' }
  }
  return {
    ...step,
    state: 'done',
    tone: 'clear',
    line: `Rechecked every ${plural(timing.intervalDays, 'day')} · next ${date(next, timing)}`,
  }
}

/** When the steps are read, and the backend's recheck interval, with date formatting options. */
export type StepTiming = FormatOptions & { now: Date; intervalDays: number }

/** Identity, Screening and Monitoring, in that order, from the backend's status and verdict. */
export function providerSteps(
  status: Pick<ProviderStatus, 'identity' | 'last_checked_at' | 'next_check_at'> &
    Partial<Pick<ProviderStatus, 'checking'>>,
  verdict: VerdictFigures,
  timing: StepTiming,
): Step[] {
  return [
    identityStep(status.identity),
    screeningStep(verdict, status.checking ?? false),
    monitoringStep(status, verdict, timing),
  ]
}
