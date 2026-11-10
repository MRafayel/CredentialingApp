/**
 * How the NPI registry reads on screen: names, places and provenance of mirror rows, the
 * words for match evidence, and which rows of a history or a link list matter. Nothing here
 * decides a status: statuses, levels and freshness arrive from the backend and are only named.
 */
import type {
  LiveCheck,
  MatchEvidence,
  MirrorSource,
  NpiLink,
  NpiSummary,
  Verification,
} from './api.ts'

/** Ten digits: the only shape a query can take to be an NPI. */
export const isNpi = (value: string) => /^\d{10}$/.test(value.trim())

/** The registry's name for an NPI: "Last, First Middle Suffix", the organization, or nothing. */
export function registryName(
  record: Pick<NpiSummary, 'person_name' | 'organization_name'>,
): string | null {
  const name = record.person_name
  if (name) {
    const given = [name.first, name.middle].filter(Boolean).join(' ')
    const full = given ? `${name.last}, ${given}` : name.last
    return name.suffix ? `${full} ${name.suffix}` : full
  }
  return record.organization_name ?? null
}

/** "Austin, TX": the practice place the mirror keeps (never a street). */
export function registryPlace(
  record: Pick<NpiSummary, 'practice_city' | 'practice_state'>,
): string | null {
  const parts = [record.practice_city, record.practice_state].filter(Boolean)
  return parts.length ? parts.join(', ') : null
}

/** The source behind a mirror or live block, named for people. */
export function registrySourceName(sourceId: string): string {
  switch (sourceId) {
    case 'nppes_monthly':
      return 'NPPES monthly file'
    case 'nppes_weekly':
      return 'NPPES weekly file'
    case 'nppes_deactivations':
      return 'NPPES deactivation file'
    case 'nppes_api':
      return 'NPPES live registry'
    default:
      return sourceId
  }
}

/**
 * The date a mirror block is "as of": the file's own date when CMS states one, else when it was
 * fetched. A live block is as of the moment the registry answered.
 */
export function asOf(block: Pick<MirrorSource | LiveCheck, 'provenance'>): string {
  return block.provenance.source_updated_at ?? block.provenance.verified_at
}

const FIELD_WORDS: Record<MatchEvidence['field'], string> = {
  npi: 'NPI',
  last_name: 'last name',
  first_name: 'first name',
  middle_name: 'middle name',
  date_of_birth: 'date of birth',
  license: 'licence',
  address: 'address',
  organization_name: 'organization name',
}

export type EvidenceWords = { matched: string[]; differs: string[] }

/**
 * Match evidence as a few words: what agreed ("same ZIP", "first name") and what did not
 * ("licence: another number in NY"). The address detail already says how it matched, so it
 * stands alone; the other fields are named.
 */
export function evidenceWords(evidence: readonly MatchEvidence[]): EvidenceWords {
  const matched: string[] = []
  const differs: string[] = []
  for (const item of evidence) {
    const word = FIELD_WORDS[item.field]
    if (item.matched) {
      matched.push(item.field === 'address' && item.detail ? item.detail : word)
    } else {
      differs.push(item.detail ? `${word}: ${item.detail}` : word)
    }
  }
  return { matched, differs }
}

/** The newest NPI verification in a history (newest first or not), or null. */
export function latestNpiVerification(history: readonly Verification[]): Verification | null {
  let newest: Verification | null = null
  for (const result of history) {
    if (result.credential_type !== 'npi') continue
    if (!newest || Date.parse(result.created_at) > Date.parse(newest.created_at)) newest = result
  }
  return newest
}

/**
 * The link a record page leads with: the strongest one that points at an NPI. Links arrive
 * best first; the one row of a record linked to nothing (`npi` null) is never a suggestion.
 */
export function bestLink(links: readonly NpiLink[]): NpiLink | null {
  return links.find((link) => link.npi !== null) ?? null
}

/** Whether a level is one a person should weigh before relying on it. */
export const needsReview = (level: NpiLink['level']) =>
  level === 'potential' || level === 'ambiguous'
