/** How a provider is named and described on screen. */
import type { Provider } from './api.ts'

/** What the add-provider form starts from when it is opened prefilled. */
export type ProviderDraft = {
  kind: 'individual' | 'organization'
  last: string
  first: string
  middle: string
  suffix: string
  organization: string
  npi: string
  licences: { number: string; state: string }[]
}

/** "Sampleperson, Avery J Jr" for a person, the organization's name otherwise. */
export function displayName(provider: Pick<Provider, 'person_name' | 'organization_name'>): string {
  const name = provider.person_name
  if (name) {
    const given = [name.first, name.middle, name.suffix].filter(Boolean).join(' ')
    return given ? `${name.last}, ${given}` : name.last
  }
  return provider.organization_name ?? 'Unnamed provider'
}

/** Every state the provider is tied to by a licence or an address, sorted. */
export function providerStates(provider: Pick<Provider, 'licenses' | 'addresses'>): string[] {
  const states = new Set<string>()
  for (const licence of provider.licenses) states.add(licence.state)
  for (const address of provider.addresses) if (address.state) states.add(address.state)
  return [...states].sort()
}

/** The sources a check of this provider consults: federal lists and their states' lists. */
export function expectedSources<S extends { jurisdictions: string[] }>(
  sources: readonly S[],
  provider: Pick<Provider, 'licenses' | 'addresses'>,
): S[] {
  const covered = new Set(['US', ...providerStates(provider)])
  return sources.filter((source) => source.jurisdictions.some((j) => covered.has(j)))
}

/** The kind of party in words; a source that does not say gives "Unknown kind". */
export function entityKindWord(kind: Provider['entity_kind']): string {
  switch (kind) {
    case 'organization':
      return 'Organization'
    case 'individual':
      return 'Individual'
    case 'unknown':
      return 'Unknown kind'
  }
}
