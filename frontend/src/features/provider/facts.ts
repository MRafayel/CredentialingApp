/**
 * What the registry adds to the provider's Facts once the NPI is known: only what the roster does
 * not already say. The roster's own rows stay as entered; each registry row added here is shown
 * as NPPES's. Nothing here decides a status.
 */
import type { NpiRecord, Provider } from '../../lib/api.ts'
import { registryName, registryPlace } from '../../lib/registry.ts'

export type RegistryTaxonomy = NpiRecord['taxonomies'][number]
export type RegistryLicence = { state: string; number: string }

/** A state and a licence number as letters and digits only, upper case (as the backend compares). */
const licenceKey = (state: string, number: string) =>
  `${state.trim().toUpperCase()}|${number.replace(/[^0-9a-z]/gi, '').toUpperCase()}`

const fold = (value: string | null | undefined) => (value ?? '').trim().toUpperCase()

/**
 * The registry's name when it reads differently from the roster's ("Last, First Middle Suffix"
 * or the organization), compared without regard to case; null when they agree or either is
 * missing.
 */
export function registryNameIfDifferent(
  provider: Pick<Provider, 'person_name' | 'organization_name'>,
  record: Pick<NpiRecord, 'person_name' | 'organization_name'>,
): string | null {
  const theirs = registryName(record)
  const ours = registryName(provider)
  if (!theirs || !ours) return null
  return fold(theirs) === fold(ours) ? null : theirs
}

/**
 * The licences the registry's taxonomies report (state and number both given) that the roster
 * does not list: same state and the same letters and digits, ignoring case and punctuation.
 * Each licence once, in the registry's order with the primary taxonomy's first.
 */
export function addedLicences(
  provider: Pick<Provider, 'licenses'>,
  record: Pick<NpiRecord, 'taxonomies'>,
): RegistryLicence[] {
  const seen = new Set(
    provider.licenses.map((licence) => licenceKey(licence.state, licence.number)),
  )
  const added: RegistryLicence[] = []
  for (const taxonomy of primaryFirst(record.taxonomies)) {
    const state = taxonomy.license_state?.trim()
    const number = taxonomy.license_number?.trim()
    if (!state || !number) continue
    const key = licenceKey(state, number)
    if (seen.has(key)) continue
    seen.add(key)
    added.push({ state, number })
  }
  return added
}

/**
 * The registry's practice place ("Austin, TX") when no roster address already has that city and
 * state; null otherwise.
 */
export function addedPlace(
  provider: Pick<Provider, 'addresses'>,
  record: Pick<NpiRecord, 'practice_city' | 'practice_state'>,
): string | null {
  const place = registryPlace(record)
  if (!place) return null
  const city = fold(record.practice_city)
  const state = fold(record.practice_state)
  const known = provider.addresses.some(
    (address) => fold(address.city) === city && fold(address.state) === state,
  )
  return known ? null : place
}

/**
 * The registry's taxonomies whose code the roster does not list, primary first, each code,
 * licence state and number once.
 */
export function addedTaxonomies(
  provider: Pick<Provider, 'taxonomy_codes'>,
  record: Pick<NpiRecord, 'taxonomies'>,
): RegistryTaxonomy[] {
  const listed = new Set(provider.taxonomy_codes.map(fold))
  const seen = new Set<string>()
  const added: RegistryTaxonomy[] = []
  for (const taxonomy of primaryFirst(record.taxonomies)) {
    if (listed.has(fold(taxonomy.code))) continue
    const key = [
      fold(taxonomy.code),
      fold(taxonomy.license_state),
      (taxonomy.license_number ?? '').replace(/[^0-9a-z]/gi, '').toUpperCase(),
    ].join('|')
    if (seen.has(key)) continue
    seen.add(key)
    added.push(taxonomy)
  }
  return added
}

/** The registry's taxonomies with the primary one first, the rest in their order. */
function primaryFirst(taxonomies: readonly RegistryTaxonomy[]): RegistryTaxonomy[] {
  return [...taxonomies].sort((a, b) => Number(b.primary) - Number(a.primary))
}
