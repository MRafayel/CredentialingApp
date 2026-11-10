/**
 * How the lookup talks to the NPI registry: which queries are names, the live search's fields
 * prefilled from what was typed, and the words for a link the backend found nothing for. Nothing
 * here matches or ranks: the backend's mirror and the live registry do.
 */
import type { NpiSearchQuery, NpiSummary } from '../../lib/api.ts'
import { entityKindWord } from '../../lib/provider.ts'
import { isNpi, registryPlace } from '../../lib/registry.ts'

/** How many registry rows a name lookup shows from the mirror. */
export const NAME_LIMIT = 10

/** A query that can be a name: not an NPI, at least two letters and no run of digits. */
export function isNameQuery(query: string): boolean {
  const q = query.trim()
  if (isNpi(q) || /\d{3,}/.test(q)) return false
  return (q.match(/\p{L}/gu)?.length ?? 0) >= 2
}

/** Words that mark an organization's name (legal designators and common kinds of practice). */
const ORGANIZATION_WORDS = new Set([
  'INC',
  'LLC',
  'LLP',
  'PLLC',
  'PC',
  'PA',
  'LTD',
  'CORP',
  'CORPORATION',
  'COMPANY',
  'CO',
  'GROUP',
  'ASSOCIATES',
  'PHARMACY',
  'CLINIC',
  'HOSPITAL',
  'CENTER',
  'CENTRE',
  'HEALTH',
  'HEALTHCARE',
  'MEDICAL',
  'SERVICES',
  'CARE',
  'LABORATORY',
  'LABS',
  'SUPPLY',
  'AGENCY',
  'THERAPY',
  'DENTAL',
  'AMBULANCE',
  'TRANSPORT',
  'HOME',
])

/** Whether a typed query reads as an organization's name ("Acme Pharmacy Inc"). */
export function looksLikeOrganization(query: string): boolean {
  return query
    .toUpperCase()
    .split(/[^\p{L}]+/u)
    .some((word) => ORGANIZATION_WORDS.has(word))
}

/** The live search's fields as the sheet holds them. */
export type LiveDraft = {
  kind: 'individual' | 'organization'
  last: string
  first: string
  organization: string
  state: string
  city: string
}

/**
 * The live search's fields from what the lookup searched: "Last, First" splits at the comma;
 * "First Last" puts the last word in the last name and the rest in the first; one word is a
 * last name; an organization-looking query fills the organization's name.
 */
export function liveDraft(query: string): LiveDraft {
  const q = query.trim().replace(/\s+/g, ' ')
  const blank: LiveDraft = {
    kind: 'individual',
    last: '',
    first: '',
    organization: '',
    state: '',
    city: '',
  }
  if (!q) return blank
  if (looksLikeOrganization(q)) return { ...blank, kind: 'organization', organization: q }
  const comma = q.indexOf(',')
  if (comma >= 0) {
    return { ...blank, last: q.slice(0, comma).trim(), first: q.slice(comma + 1).trim() }
  }
  const words = q.split(' ')
  const last = words.pop() ?? ''
  return { ...blank, last, first: words.join(' ') }
}

const blankToUndefined = (value: string) => value.trim() || undefined

/** The live request the sheet sends: the fields of the chosen kind, blanks left out. */
export function liveQuery(draft: LiveDraft, limit: number): NpiSearchQuery {
  const person = draft.kind === 'individual'
  return {
    live: true,
    entity_kind: draft.kind,
    last_name: person ? blankToUndefined(draft.last) : undefined,
    first_name: person ? blankToUndefined(draft.first) : undefined,
    organization_name: person ? undefined : blankToUndefined(draft.organization),
    state: blankToUndefined(draft.state),
    city: blankToUndefined(draft.city),
    limit,
  }
}

/**
 * Why a record links to no NPI, as a few words: the backend's explanation without its
 * "No match: " lead and final period, starting with a capital ("No candidate with this name").
 */
export function linkReason(explanation: string): string {
  const text = explanation
    .trim()
    .replace(/^no match:\s*/i, '')
    .replace(/\.$/, '')
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : explanation
}

/** "Individual · MD · Austin, TX": what a registry row says about the party, never a street. */
export function summaryFacts(
  record: Pick<NpiSummary, 'entity_kind' | 'credential' | 'practice_city' | 'practice_state'>,
): string[] {
  return [
    record.entity_kind === 'unknown' ? null : entityKindWord(record.entity_kind),
    record.credential,
    registryPlace(record),
  ].filter((part): part is string => Boolean(part))
}
