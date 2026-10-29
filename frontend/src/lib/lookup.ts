/**
 * How a direct exclusion lookup reads on screen: the action as a status, which lists were
 * searched and how old each is, and the draft a found record turns into. Nothing here
 * re-matches or re-ranks: the backend decides what matched and in which order.
 */
import type { ExclusionAction, LookupRecord, SearchedSource } from './api.ts'
import { date, plural, type FormatOptions } from './format.ts'
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

export type ListAge = {
  sourceId: string
  name: string
  /** `updated`: the source's own date; `fetched`: it publishes none, so when we fetched it. */
  kind: 'updated' | 'fetched' | 'not_loaded'
  at: string | null
}

/** Each list's age, in the order the backend gave them. */
export function listAges(sources: readonly SearchedSource[]): ListAge[] {
  return sources.map((source) => {
    const base = { sourceId: source.source_id, name: source.display_name }
    if (source.list_last_updated) {
      return { ...base, kind: 'updated', at: source.list_last_updated }
    }
    if (source.fetched_at) return { ...base, kind: 'fetched', at: source.fetched_at }
    return { ...base, kind: 'not_loaded', at: null }
  })
}

/** How many lists were actually searched: a list never loaded was not. */
export function searchedCount(sources: readonly SearchedSource[]): number {
  return sources.filter((source) => source.fetched_at !== null).length
}

/** "<list> updated Oct 9, 2026", "<list> fetched Oct 26, 2026" or "<list> not loaded, not searched". */
export function listAgeText(age: ListAge, options: FormatOptions = {}): string {
  switch (age.kind) {
    case 'updated':
      return `${age.name} updated ${date(age.at ?? '', options)}`
    case 'fetched':
      return `${age.name} fetched ${date(age.at ?? '', options)}`
    case 'not_loaded':
      return `${age.name} not loaded, not searched`
  }
}

/** "Searched 2 lists · <list> updated Oct 9, 2026 · <list> fetched Oct 26, 2026". */
export function searchedLine(sources: readonly SearchedSource[], options: FormatOptions = {}) {
  return [
    `Searched ${plural(searchedCount(sources), 'list')}`,
    ...listAges(sources).map((age) => listAgeText(age, options)),
  ].join(' · ')
}

/**
 * The calm empty answer: what was searched, never just "nothing". `place` is the state the
 * search was narrowed to ("No matches in Washington across 11 lists").
 */
export function noMatchesLine(sources: readonly SearchedSource[], place?: string | null): string {
  const lists = plural(searchedCount(sources), 'list')
  return place ? `No matches in ${place} across ${lists}` : `No matches in ${lists}`
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
