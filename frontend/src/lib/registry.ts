/**
 * How the NPI registry reads on screen: names, places and provenance of mirror rows, the
 * words for match evidence, and which row of a link list matters. Nothing here
 * decides a status: statuses, levels and freshness arrive from the backend and are only named.
 */
import type {
  LiveCheck,
  MatchEvidence,
  MirrorSource,
  NpiLink,
  NpiLookup,
  NpiSummary,
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

/** The NPPES search portal's own page for an NPI, where a person can see the official record. */
export const npiPortalUrl = (npi: string) => `https://npiregistry.cms.hhs.gov/provider-view/${npi}`

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

/** Where a lookup's registry facts come from, with the kind of line that names it. */
export type FactsSource =
  { kind: 'live'; block: LiveCheck } | { kind: 'mirror'; block: MirrorSource }

/**
 * The block a lookup's record was read from: the live answer when the registry returned the NPI
 * (the backend then sends that answer as the record), else the stored file. A live request that
 * failed or found nothing leaves the facts on the file, never "live".
 */
export function factsSource(lookup: Pick<NpiLookup, 'live' | 'mirror'>): FactsSource | null {
  const live = lookup.live
  if (live?.outcome === 'completed' && live.found === true) return { kind: 'live', block: live }
  return lookup.mirror ? { kind: 'mirror', block: lookup.mirror } : null
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
 * stands alone; a matched licence keeps its detail too, since the backend says there how
 * the numbers agreed ("same number 10213: 10213 and PSY10213", "same licence state") and a
 * person must see what was set aside (owner, 2026-11-12); the other fields are named.
 */
export function evidenceWords(evidence: readonly MatchEvidence[]): EvidenceWords {
  const matched: string[] = []
  const differs: string[] = []
  for (const item of evidence) {
    const word = FIELD_WORDS[item.field]
    if (item.matched) {
      if (item.field === 'address' && item.detail) matched.push(item.detail)
      else if (item.field === 'license' && item.detail) matched.push(`${word}: ${item.detail}`)
      else matched.push(word)
    } else {
      differs.push(item.detail ? `${word}: ${item.detail}` : word)
    }
  }
  return { matched, differs }
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
