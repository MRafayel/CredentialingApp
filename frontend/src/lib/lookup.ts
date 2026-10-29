/**
 * How a direct exclusion lookup reads on screen: the action as a status, where it searched,
 * and the draft a found record turns into. Nothing here
 * re-matches or re-ranks: the backend decides what matched and in which order.
 */
import type { ExclusionAction, LookupRecord, SearchedSource } from './api.ts'
import { plural } from './format.ts'
import type { ProviderDraft } from './provider.ts'
import type { Label } from './labels.ts'

/** The lookup searches nothing below this many characters (the backend's own minimum). */
export const MIN_LOOKUP_LENGTH = 2

/** Whether a typed query is long enough to search. */
export function isSearchable(query: string): boolean {
  return query.trim().length >= MIN_LOOKUP_LENGTH
}

const ACTION_WORDS: Record<ExclusionAction, string> = {
  exclusion: 'Exclusion',
  termination: 'Termination',
  suspension: 'Suspension',
  debarment: 'Debarment',
  preclusion: 'Preclusion',
  sanction: 'Sanction',
  reinstatement: 'Reinstatement',
  other: 'Other action',
}

/** The listed action as a pill: every restrictive action in the listed tone. */
export function actionLabel(action: ExclusionAction): Label {
  switch (action) {
    case 'reinstatement':
      return { tone: 'neutral', word: ACTION_WORDS[action] }
    case 'other':
      return { tone: 'review', word: ACTION_WORDS[action] }
    default:
      return { tone: 'listed', word: ACTION_WORDS[action] }
  }
}

/** The federal jurisdiction: a list that covers every state. */
const FEDERAL = 'US'

const isState = (code: string) => code !== FEDERAL && /^[A-Z]{2}$/.test(code)

/** How many lists were actually searched: a list never loaded was not. */
export function searchedCount(sources: readonly SearchedSource[]): number {
  return sources.filter((source) => source.fetched_at !== null).length
}

/** The state a lookup was narrowed to: its code (the lists' jurisdiction) and its name. */
export type SearchedState = { code: string; name: string }

/**
 * What a lookup covered, as the backend reported it: where it searched ("9 states and the
 * federal list", or for a filter "in Washington · its state list and the federal list") and
 * how many lists were never loaded and so not searched.
 */
export type SearchedScope = {
  where: string
  notLoaded: number
}

/** "the federal list", "the federal lists" or null. */
function federalPhrase(searched: readonly SearchedSource[]): string | null {
  const count = searched.filter((source) => source.jurisdictions.includes(FEDERAL)).length
  if (count === 0) return null
  return count === 1 ? 'the federal list' : 'the federal lists'
}

/** Where a lookup searched; see `SearchedScope`. */
export function searchedScope(
  sources: readonly SearchedSource[],
  state?: SearchedState | null,
): SearchedScope {
  const searched = sources.filter((source) => source.fetched_at !== null)
  const federal = federalPhrase(searched)
  if (!state) {
    const states = new Set(searched.flatMap((source) => source.jurisdictions.filter(isState)))
    const parts = [states.size > 0 ? plural(states.size, 'state') : null, federal]
    const where = parts.filter(Boolean).join(' and ')
    return {
      where: where ? `Searched ${where}` : 'Searched no lists',
      notLoaded: sources.length - searched.length,
    }
  }
  const own = (source: SearchedSource) => source.jurisdictions.includes(state.code)
  const ownSearched = searched.filter(own)
  const ownUnloaded = sources.some((source) => own(source) && source.fetched_at === null)
  const ownPhrase =
    ownSearched.length === 0
      ? null
      : ownSearched.length === 1
        ? 'its state list'
        : 'its state lists'
  const missing = ownUnloaded ? 'its state list not loaded' : 'no state list yet'
  const lists = ownPhrase
    ? [ownPhrase, federal].filter(Boolean).join(' and ')
    : federal
      ? `${missing}, ${federal} only`
      : missing
  return {
    where: `Searched in ${state.name} · ${lists}`,
    // The state's own unloaded lists are already named above.
    notLoaded: sources.filter((source) => source.fetched_at === null && !own(source)).length,
  }
}

/** "1 list not loaded", "2 lists not loaded". */
export function notLoadedText(count: number): string {
  return `${plural(count, 'list')} not loaded`
}

/** The whole line: "Searched 9 states and the federal list · 1 list not loaded". */
export function searchedLine(
  sources: readonly SearchedSource[],
  state?: SearchedState | null,
): string {
  const { where, notLoaded } = searchedScope(sources, state)
  return notLoaded > 0 ? `${where} · ${notLoadedText(notLoaded)}` : where
}

/**
 * The calm empty answer: what was searched, never just "nothing". `place` is the state the
 * search was narrowed to ("No matches in Washington"); the searched line under it then says
 * which of its lists were searched, so no list count here contradicts it.
 */
export function noMatchesLine(sources: readonly SearchedSource[], place?: string | null): string {
  return place
    ? `No matches in ${place}`
    : `No matches in ${plural(searchedCount(sources), 'list')}`
}

/** "3 matches", "3 matches in Washington", or a hint to narrow a full page of them. */
export function matchesLine(count: number, limit: number, place?: string | null): string {
  const where = place ? ` in ${place}` : ''
  return count >= limit
    ? `The best ${count} matches${where}; add a word to narrow them`
    : `${plural(count, 'match', 'matches')}${where}`
}

/** "Sampleville, NY", "NY" or null. */
export function placeText(record: Pick<LookupRecord, 'place'>): string | null {
  const place = record.place
  if (!place) return null
  return [place.city, place.state].filter(Boolean).join(', ') || null
}

/** The add-provider form's starting point for a found record. */
export function draftFromRecord(record: LookupRecord): ProviderDraft {
  const name = record.person_name
  const organization =
    record.entity_kind === 'organization' || (!name && record.organization_name !== null)
  return {
    kind: organization ? 'organization' : 'individual',
    last: organization ? '' : (name?.last ?? ''),
    first: organization ? '' : (name?.first ?? ''),
    middle: organization ? '' : (name?.middle ?? ''),
    suffix: organization ? '' : (name?.suffix ?? ''),
    organization: organization ? (record.organization_name ?? '') : '',
    npi: record.npi ?? '',
    licences: record.license
      ? [{ number: record.license.number, state: record.license.state }]
      : [],
  }
}
