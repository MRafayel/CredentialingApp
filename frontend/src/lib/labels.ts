/**
 * How the backend's statuses read on screen: each enum value becomes a tone, a symbol and a
 * word. Nothing here decides a status; the verdict, findings, outcomes and freshness arrive
 * from the backend and are only named.
 */
import type {
  Freshness,
  MatchLevel,
  NpiStatus,
  Outcome,
  ProviderVerdict,
  RecordStatus,
  RegistryStatus,
  Verification,
} from './api.ts'
import { plural } from './format.ts'

/** The status colours; each always travels with a symbol and a word. */
export type Tone = 'clear' | 'review' | 'flagged' | 'listed' | 'blocked' | 'neutral'

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
