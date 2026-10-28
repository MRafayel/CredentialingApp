/**
 * How the backend's statuses read on screen: each enum value becomes a tone, a symbol and a
 * word. Nothing here decides a status; the verdict, findings, outcomes and freshness arrive
 * from the backend and are only named.
 */
import type { Freshness, Outcome, ProviderVerdict, RecordStatus, Verification } from './api.ts'
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

/**
 * One source's result as a pill: the finding when it ran, the outcome when it could not. A
 * record of another party naming the provider (`related_party`) reads "Flagged". A found or
 * related-party record the backend marks as ended (`details.record_status`) reads "Previously
 * listed" or "Previously flagged" in the review tone, as the backend's verdict counts it.
 */
export function resultLabel(
  result: Pick<Verification, 'outcome' | 'finding'> & Partial<Pick<Verification, 'details'>>,
): Label {
  if (result.outcome !== 'completed') {
    return { tone: 'blocked', word: OUTCOME_WORDS[result.outcome] }
  }
  switch (result.finding) {
    case 'record_found':
      return result.details?.record_status === 'ended'
        ? { tone: 'review', word: 'Previously listed' }
        : { tone: 'listed', word: 'Record found' }
    case 'related_party':
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
 * "Listed", one whose end date has passed (or a reinstatement) "Previously listed", in the same
 * tones a provider's results use.
 */
export function recordStatusLabel(status: RecordStatus): Label {
  return status === 'ended'
    ? { tone: 'review', word: 'Previously listed' }
    : { tone: 'listed', word: 'Listed' }
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
