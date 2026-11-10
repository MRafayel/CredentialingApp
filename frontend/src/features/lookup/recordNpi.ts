/**
 * What a record page says about its NPI, from the record's published NPI and the links the
 * backend inferred to the registry mirror. The backend decides every link and level; this only
 * picks which of its rows the page leads with.
 */
import type { NpiLink, NpiSummary, RecordProfile } from '../../lib/api.ts'
import { bestLink } from '../../lib/registry.ts'

/** A link that points at a registry row. */
export type RegistryLinkRow = NpiLink & { npi: string; registry: NpiSummary }

export type RecordNpi =
  /** Never linked: the published NPI as it stands, or nothing published. */
  | { kind: 'unlinked'; published: string | null }
  /** The published NPI and what the registry's row says about it. */
  | { kind: 'published'; npi: string; link: RegistryLinkRow }
  /** The published NPI, which the registry does not hold. */
  | { kind: 'not_in_nppes'; npi: string; link: NpiLink }
  /** No published NPI; the registry rows the backend linked, best first. */
  | { kind: 'candidates'; best: RegistryLinkRow; others: RegistryLinkRow[] }
  /** No published NPI and nothing linked; the link row says why. */
  | { kind: 'no_candidate'; link: NpiLink }

const pointsAtRow = (link: NpiLink): link is RegistryLinkRow =>
  link.npi !== null && link.registry !== null

export function recordNpi(profile: Pick<RecordProfile, 'record' | 'npi_links'>): RecordNpi {
  const published = profile.record.npi
  const links = profile.npi_links
  const nothing = links.find((link) => link.npi === null)
  if (published) {
    const own = links.find((link) => link.npi === published)
    if (own && pointsAtRow(own)) return { kind: 'published', npi: published, link: own }
    if (!own && nothing) return { kind: 'not_in_nppes', npi: published, link: nothing }
    return { kind: 'unlinked', published }
  }
  const best = bestLink(links)
  if (best && pointsAtRow(best)) {
    const others = links.filter((link) => link !== best).filter(pointsAtRow)
    return { kind: 'candidates', best, others }
  }
  if (nothing) return { kind: 'no_candidate', link: nothing }
  return { kind: 'unlinked', published: null }
}
