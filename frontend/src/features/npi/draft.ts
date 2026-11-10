/** The add-provider form's starting point for an NPI as the registry records it. */
import type { NpiRecord } from '../../lib/api.ts'
import type { ProviderDraft } from '../../lib/provider.ts'

/**
 * The registry's name, the NPI and the licences reported with its taxonomies (each number and
 * state once). An organization, or a row with only an organization's name, opens as one.
 */
export function draftFromNpi(
  record: Pick<
    NpiRecord,
    'npi' | 'entity_kind' | 'person_name' | 'organization_name' | 'taxonomies'
  >,
): ProviderDraft {
  const name = record.person_name
  const organization =
    record.entity_kind === 'organization' || (!name && record.organization_name !== null)
  const seen = new Set<string>()
  const licences: ProviderDraft['licences'] = []
  for (const taxonomy of record.taxonomies) {
    const number = taxonomy.license_number?.trim()
    const state = taxonomy.license_state?.trim().toUpperCase()
    if (!number || !state || !/^[A-Z]{2}$/.test(state)) continue
    const key = `${state} ${number}`
    if (seen.has(key)) continue
    seen.add(key)
    licences.push({ number, state })
  }
  return {
    kind: organization ? 'organization' : 'individual',
    last: organization ? '' : (name?.last ?? ''),
    first: organization ? '' : (name?.first ?? ''),
    middle: organization ? '' : (name?.middle ?? ''),
    suffix: organization ? '' : (name?.suffix ?? ''),
    organization: organization ? (record.organization_name ?? '') : '',
    npi: record.npi,
    licences,
  }
}
