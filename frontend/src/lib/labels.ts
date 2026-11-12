/**
 * How the backend's statuses read on screen: each enum value becomes a tone, a symbol and a
 * word. Nothing here decides a status; the verdict, findings, outcomes and freshness arrive
 * from the backend and are only named.
 */
import type {
  Attention,
  Freshness,
  IdentityState,
  MatchLevel,
  NpiStatus,
  Outcome,
  ProviderStatus,
  ProviderVerdict,
  RecordStatus,
  RegistryStatus,
  ResultChange,
  RosterRole,
  Verification,
} from './api.ts'
import { plural } from './format.ts'

/**
 * The status colours; each always travels with a symbol and a word. `changed` is the accent with
 * its own mark (a result that differs from the one before it), never the clear shield.
 */
export type Tone = 'clear' | 'review' | 'flagged' | 'listed' | 'blocked' | 'neutral' | 'changed'

export type Label = { tone: Tone; word: string; detail?: string }

type VerdictFigures = Pick<
  ProviderVerdict,
  'verdict' | 'sources_checked' | 'listed_in' | 'flagged_in' | 'review_in' | 'unchecked'
>

/** The backend's verdict, with the figure it gives for that verdict. */
export function verdictLabel(verdict: VerdictFigures): Label {
  switch (verdict.verdict) {
    case 'listed':
      return {
        tone: 'listed',
        word: 'Listed',
        detail: `in ${plural(verdict.listed_in.length, 'list')}`,
      }
    case 'flagged':
      return {
        tone: 'flagged',
        word: 'Flagged',
        detail: `in ${plural(verdict.flagged_in.length, 'list')}`,
      }
    case 'needs_review':
      return {
        tone: 'review',
        word: 'Needs review',
        detail: `in ${plural(verdict.review_in.length, 'list')}`,
      }
    case 'could_not_check':
      return { tone: 'blocked', word: `Could not check ${verdict.unchecked.length}` }
    case 'no_records':
      return {
        tone: 'clear',
        word: 'No records',
        detail: `in ${plural(verdict.sources_checked, 'list')}`,
      }
    case 'not_checked':
      return { tone: 'neutral', word: 'Not checked' }
  }
}

const OUTCOME_WORDS: Record<Exclude<Outcome, 'completed'>, string> = {
  source_unavailable: 'Source unavailable',
  blocked: 'Blocked',
  manual_required: 'Manual required',
  failed: 'Failed',
}

/** The word for a record the backend marks as removed from its published list. */
const REMOVED_WORD = 'Removed from published list'

/**
 * One source's result as a pill: the finding when it ran, the outcome when it could not. A
 * record of another party naming the provider (`related_party`) reads "Flagged". A found or
 * related-party record the backend marks as ended (`details.record_status`) reads "Previously
 * listed" or "Previously flagged", and one marked as removed from its published list "Removed
 * from published list", in the review tone, as the backend's verdict counts them.
 */
export function resultLabel(
  result: Pick<Verification, 'outcome' | 'finding'> & Partial<Pick<Verification, 'details'>>,
): Label {
  // An NPI verification (credential type `npi`) names the registry's status instead.
  const npiStatus = result.details?.npi_status
  if (npiStatus && isRegistryStatus(npiStatus)) return registryStatusLabel(npiStatus)
  if (result.outcome === 'manual_required' && result.details?.reason === 'no_npi') {
    return { tone: 'review', word: 'No NPI on file' }
  }
  if (result.outcome !== 'completed') {
    return { tone: 'blocked', word: OUTCOME_WORDS[result.outcome] }
  }
  switch (result.finding) {
    case 'record_found':
      if (result.details?.record_status === 'removed') return { tone: 'review', word: REMOVED_WORD }
      return result.details?.record_status === 'ended'
        ? { tone: 'review', word: 'Previously listed' }
        : { tone: 'listed', word: 'Record found' }
    case 'related_party':
      if (result.details?.record_status === 'removed') return { tone: 'review', word: REMOVED_WORD }
      return result.details?.record_status === 'ended'
        ? { tone: 'review', word: 'Previously flagged' }
        : { tone: 'flagged', word: 'Flagged' }
    case 'ambiguous':
      return { tone: 'review', word: 'Needs review' }
    case 'no_record':
      return { tone: 'clear', word: 'No record' }
    default:
      return { tone: 'blocked', word: 'No finding' }
  }
}

/**
 * A listed record's own status, as the backend decided it: a record that still stands reads
 * "Listed", one whose end date has passed (or a reinstatement) "Previously listed", and one no
 * longer on its published list (reinstatement not confirmed) "Removed from published list", in
 * the same tones a provider's results use.
 */
export function recordStatusLabel(status: RecordStatus): Label {
  switch (status) {
    case 'active':
      return { tone: 'listed', word: 'Listed' }
    case 'ended':
      return { tone: 'review', word: 'Previously listed' }
    case 'removed':
      return { tone: 'review', word: REMOVED_WORD }
  }
}

/** Words for a fetch outcome the backend recorded in `details` ("source_unavailable"). */
export function outcomeWord(value: string): string {
  return value in OUTCOME_WORDS
    ? OUTCOME_WORDS[value as keyof typeof OUTCOME_WORDS]
    : value.replaceAll('_', ' ')
}

export function freshnessLabel(freshness: Freshness): Label {
  switch (freshness) {
    case 'live':
      return { tone: 'clear', word: 'Live' }
    case 'cached':
      return { tone: 'neutral', word: 'Cached' }
    case 'stale':
      return { tone: 'review', word: 'Stale' }
    case 'manual':
      return { tone: 'neutral', word: 'Manually verified' }
  }
}

// --- The NPI registry -----------------------------------------------------------------------

const REGISTRY_STATUSES: readonly RegistryStatus[] = [
  'active',
  'deactivated',
  'not_found',
  'mirror_only_active',
  'unknown',
]

function isRegistryStatus(value: string): value is RegistryStatus {
  return (REGISTRY_STATUSES as readonly string[]).includes(value)
}

/**
 * The backend's one-word conclusion about an NPI. "Not in NPPES" and "Needs review" (the mirror
 * lists it active, the live registry returned nothing) are amber; "Could not check" is never
 * clean.
 */
export function registryStatusLabel(status: RegistryStatus): Label {
  switch (status) {
    case 'active':
      return { tone: 'clear', word: 'Active' }
    case 'deactivated':
      return { tone: 'listed', word: 'Deactivated' }
    case 'not_found':
      return { tone: 'review', word: 'Not in NPPES' }
    case 'mirror_only_active':
      return { tone: 'review', word: 'Needs review' }
    case 'unknown':
      return { tone: 'blocked', word: 'Could not check' }
  }
}

/** A registry row's own status, as the NPPES files publish it. */
export function npiStatusLabel(status: NpiStatus): Label {
  return status === 'active'
    ? { tone: 'clear', word: 'Active' }
    : { tone: 'listed', word: 'Deactivated' }
}

/**
 * How strongly a record or a roster provider points at an NPI, as the backend decided. Exact
 * and high read clear; potential and ambiguous are for a person to weigh (amber); none is quiet.
 */
export function matchLevelLabel(level: MatchLevel): Label {
  switch (level) {
    case 'exact':
      return { tone: 'clear', word: 'Exact' }
    case 'high':
      return { tone: 'clear', word: 'High' }
    case 'potential':
      return { tone: 'review', word: 'Potential' }
    case 'ambiguous':
      return { tone: 'review', word: 'Ambiguous' }
    case 'none':
      return { tone: 'neutral', word: 'No match' }
  }
}

// --- Monitoring -----------------------------------------------------------------------------

/** The reasons a provider needs a person, most serious first: the backend's own order. */
export const ATTENTION_ORDER: readonly Attention[] = [
  'listed',
  'flagged',
  'needs_review',
  'could_not_check',
  'changed',
  'npi_deactivated',
  'npi_uncertain',
  'npi_missing',
  'not_checked',
]

const ATTENTION_WORDS: Record<Attention, Label> = {
  listed: { tone: 'listed', word: 'Listed' },
  flagged: { tone: 'flagged', word: 'Flagged' },
  needs_review: { tone: 'review', word: 'Review' },
  could_not_check: { tone: 'blocked', word: 'Could not check' },
  changed: { tone: 'changed', word: 'Changed' },
  npi_deactivated: { tone: 'listed', word: 'NPI deactivated' },
  npi_uncertain: { tone: 'review', word: 'NPI unclear' },
  npi_missing: { tone: 'review', word: 'Needs NPI' },
  not_checked: { tone: 'neutral', word: 'Not checked' },
}

/** One reason for attention as a mark and a word: a roster filter, a row's status. */
export function attentionWord(attention: Attention): Label {
  return ATTENTION_WORDS[attention]
}

/** Sources by name: the first named, the rest counted ("OIG LEIE and 2 more"). */
export function listNames(ids: readonly string[], name: (id: string) => string): string {
  const [first] = ids
  if (first === undefined) return ''
  return ids.length === 1 ? name(first) : `${name(first)} and ${ids.length - 1} more`
}

type StatusFigures = Pick<
  ProviderStatus,
  'attention' | 'listed_in' | 'flagged_in' | 'review_in' | 'unchecked'
>

/**
 * A roster row's one word and its line, from the backend's one reason for attention: what is so
 * and where ("Listed · OIG LEIE", "Could not check · 3 lists"). A provider with no reason reads
 * "No records", the verdict's own word (never "Clear": the lists cannot promise more); the row
 * adds when it was checked.
 */
export function attentionLabel(status: StatusFigures, name: (id: string) => string): Label {
  if (status.attention === null) return { tone: 'clear', word: 'No records' }
  const label = ATTENTION_WORDS[status.attention]
  switch (status.attention) {
    case 'listed':
      return { ...label, detail: listNames(status.listed_in, name) }
    case 'flagged':
      return { ...label, detail: `named in ${listNames(status.flagged_in, name)}` }
    case 'needs_review':
      return { ...label, detail: `possible match in ${listNames(status.review_in, name)}` }
    case 'could_not_check':
      return { ...label, detail: plural(status.unchecked.length, 'list') }
    case 'changed':
      return { ...label, detail: 'since you last looked' }
    case 'npi_deactivated':
      return { ...label, detail: 'in NPPES' }
    case 'npi_uncertain':
      return { ...label, detail: 'NPPES answer to review' }
    case 'npi_missing':
    case 'not_checked':
      return label
  }
}

/** What the roster knows about the provider's NPI, as the backend decided it. */
export function identityLabel(identity: IdentityState): Label {
  switch (identity) {
    case 'npi_active':
      return { tone: 'clear', word: 'NPI confirmed', detail: 'active' }
    case 'npi_unverified':
      return { tone: 'neutral', word: 'NPI not verified' }
    case 'npi_missing':
      return { tone: 'review', word: 'Needs NPI' }
    case 'npi_deactivated':
      return { tone: 'listed', word: 'NPI deactivated' }
    case 'npi_uncertain':
      return { tone: 'review', word: 'NPI unclear', detail: 'NPPES answer to review' }
    case 'not_expected':
      return { tone: 'neutral', word: 'Not expected' }
  }
}

const ROLE_WORDS: Record<RosterRole, string> = {
  practitioner: 'Practitioner',
  workforce: 'Workforce',
  vendor: 'Vendor',
}

/** Why the party is on the roster. */
export function roleWord(role: RosterRole): string {
  return ROLE_WORDS[role]
}

/** What a result said before it changed, in the words its own result would use. */
export function changedFromLabel(change: ResultChange): Label {
  const details: Record<string, string> = {}
  if (change.record_status) details.record_status = change.record_status
  if (change.registry_status) details.npi_status = change.registry_status
  return resultLabel({ outcome: change.outcome, finding: change.finding, details })
}
