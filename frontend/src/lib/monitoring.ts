/**
 * The monitored roster in words: the header's "3 need you", the filters with their counts, and
 * a provider's three steps (identity, screening, monitoring). Every status comes from the
 * backend; these only name it and put it in order.
 */
import type {
  Attention,
  AttentionFilter,
  ProviderStatus,
  ProviderVerdict,
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

/** The roster's filter: everyone needing a person (the default), one reason, or everyone. */
export type RosterFilter = 'needs_you' | 'all' | Attention

/** The filter named in the URL (`?attention=`); anything unknown is the default. */
export function parseFilter(value: string | null): RosterFilter {
  if (value === 'all') return 'all'
  return (ATTENTION_ORDER as readonly string[]).includes(value ?? '')
    ? (value as Attention)
    : 'needs_you'
}

/** The `attention` the list asks the API for: `any` for "Needs you", none for "All". */
export function filterAttention(filter: RosterFilter): AttentionFilter | undefined {
  if (filter === 'needs_you') return 'any'
  return filter === 'all' ? undefined : filter
}

export type FilterChip = { filter: RosterFilter; word: string; label: Label | null; count: number }

/**
 * "Needs you" with the sum of every reason, one chip per reason that has providers (most
 * serious first), then "All". The chip in use always shows, even at 0.
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
  return [
    { filter: 'needs_you', word: 'Needs you', label: null, count: needingYou(summary) },
    ...reasons,
    { filter: 'all', word: 'All', label: null, count: summary.total },
  ]
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

/** The screening step only reads the verdict: "Run check" and "Check again" are the hero's. */
function screeningStep(verdict: VerdictFigures): Step {
  const step = { key: 'screening', title: 'Screening', action: null } as const
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
  status: Pick<ProviderStatus, 'identity' | 'last_checked_at' | 'next_check_at'>,
  verdict: VerdictFigures,
  timing: StepTiming,
): Step[] {
  return [
    identityStep(status.identity),
    screeningStep(verdict),
    monitoringStep(status, verdict, timing),
  ]
}
