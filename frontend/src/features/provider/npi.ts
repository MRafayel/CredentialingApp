/**
 * How the provider's NPI card reads the backend's answers: which of its three shapes it takes,
 * and a few words for the suggestions. Nothing here decides a status: the registry status, the
 * match level and the freshness arrive from the backend and are only named or arranged.
 */
import type { NpiSuggestion, Provider, Verification } from '../../lib/api.ts'

/**
 * The card's shape: an NPI with its latest verification, an NPI never verified, or no NPI. A
 * "No NPI on file" verification left from before the NPI was set does not verify it.
 */
export type NpiShape =
  | { kind: 'verified'; npi: string; result: Verification }
  | { kind: 'unverified'; npi: string }
  | { kind: 'missing' }

export function npiShape(npi: string | null, latest: Verification | null): NpiShape {
  if (!npi) return { kind: 'missing' }
  if (!latest || latest.details.reason === 'no_npi') return { kind: 'unverified', npi }
  return { kind: 'verified', npi, result: latest }
}

/** Whether a result or a block rests on the live registry rather than a file of the mirror. */
export const restsOnLive = (sourceId: string) => sourceId === 'nppes_api'

/** A suggestion that points at an NPI (every row but the one saying why nothing was found). */
export type Candidate = NpiSuggestion & { npi: string }

const isCandidate = (suggestion: NpiSuggestion): suggestion is Candidate => suggestion.npi !== null

/** The suggestions split into the NPIs to weigh, best first, and the rows saying why none. */
export function splitSuggestions(suggestions: readonly NpiSuggestion[]): {
  candidates: Candidate[]
  reasons: NpiSuggestion[]
} {
  return {
    candidates: suggestions.filter(isCandidate),
    reasons: suggestions.filter((suggestion) => !isCandidate(suggestion)),
  }
}

/** The backend counts 51 when the search found more than 50. */
const MORE_THAN = 51

/** "3 candidates in the registry"; "More than 50 candidates in the registry" for 51. */
export function candidatesLine(found: number): string {
  if (found >= MORE_THAN) return 'More than 50 candidates in the registry'
  return `${found} ${found === 1 ? 'candidate' : 'candidates'} in the registry`
}

/**
 * The reason of a row linked to nothing, as a short phrase: "No match: no candidate with this
 * name." reads "No candidate with this name".
 */
export function reasonWords(explanation: string): string {
  const bare = explanation
    .trim()
    .replace(/^no match:\s*/i, '')
    .replace(/\.$/, '')
  return bare ? bare.charAt(0).toUpperCase() + bare.slice(1) : explanation
}

/** What the lookup's field starts from to search for the provider: "Last, First" or the name. */
export function lookupDraft(provider: Pick<Provider, 'person_name' | 'organization_name'>): string {
  const name = provider.person_name
  if (name) return name.first ? `${name.last}, ${name.first}` : name.last
  return provider.organization_name ?? ''
}
