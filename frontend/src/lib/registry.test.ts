import { describe, expect, it } from 'vitest'
import type { NpiLink } from './api.ts'
import {
  asOf,
  bestLink,
  evidenceWords,
  isNpi,
  npiPortalUrl,
  registryName,
  registryPlace,
  registrySourceName,
} from './registry.ts'

describe('registryName', () => {
  it('writes a person as "Last, First Middle Suffix" and an organization as its name', () => {
    expect(
      registryName({
        person_name: { last: 'Testperson', first: 'Avery', middle: 'J', suffix: 'Jr' },
        organization_name: null,
      }),
    ).toBe('Testperson, Avery J Jr')
    expect(
      registryName({
        person_name: { last: 'Testperson', first: '', middle: null, suffix: null },
        organization_name: null,
      }),
    ).toBe('Testperson')
    expect(registryName({ person_name: null, organization_name: 'Sampleville Care' })).toBe(
      'Sampleville Care',
    )
    expect(registryName({ person_name: null, organization_name: null })).toBeNull()
  })
})

describe('registryPlace and sources', () => {
  it('joins the practice city and state, and names each NPPES source', () => {
    expect(registryPlace({ practice_city: 'Austin', practice_state: 'TX' })).toBe('Austin, TX')
    expect(registryPlace({ practice_city: null, practice_state: 'TX' })).toBe('TX')
    expect(registryPlace({ practice_city: null, practice_state: null })).toBeNull()
    expect(registrySourceName('nppes_monthly')).toBe('NPPES monthly file')
    expect(registrySourceName('nppes_api')).toBe('NPPES live registry')
    expect(registrySourceName('other')).toBe('other')
    expect(isNpi(' 1234567893 ')).toBe(true)
    expect(isNpi('123456789')).toBe(false)
  })

  it('dates a block by the source date, else by the fetch', () => {
    const provenance = {
      source_id: 'nppes_monthly',
      source_url: 'https://example.test',
      method: 'bulk_dataset' as const,
      verified_at: '2026-11-06T10:00:00Z',
      source_updated_at: '2026-11-03T03:29:06Z',
      snapshot_id: null,
      raw_record_ref: null,
    }
    expect(asOf({ provenance })).toBe('2026-11-03T03:29:06Z')
    expect(asOf({ provenance: { ...provenance, source_updated_at: null } })).toBe(
      '2026-11-06T10:00:00Z',
    )
  })
})

describe('evidenceWords', () => {
  it('names what agreed and what differed, letting an address detail stand alone', () => {
    expect(
      evidenceWords([
        { field: 'last_name', matched: true, detail: null },
        { field: 'first_name', matched: true, detail: 'initial only' },
        { field: 'address', matched: true, detail: 'same ZIP' },
        { field: 'license', matched: false, detail: 'another number in NY' },
        { field: 'middle_name', matched: false, detail: null },
      ]),
    ).toEqual({
      matched: ['last name', 'first name', 'same ZIP'],
      differs: ['licence: another number in NY', 'middle name'],
    })
  })

  it('keeps how a licence agreed, so what was set aside is seen', () => {
    expect(
      evidenceWords([
        { field: 'license', matched: true, detail: 'same number 10213: 10213 and PSY10213' },
        { field: 'license', matched: true, detail: null },
      ]),
    ).toEqual({
      matched: ['licence: same number 10213: 10213 and PSY10213', 'licence'],
      differs: [],
    })
  })
})

describe('bestLink', () => {
  it('leads with the first link that points at an NPI, never the "nothing" row', () => {
    const none: NpiLink = {
      npi: null,
      level: 'none',
      evidence: [],
      explanation: 'No match: no candidate with this name.',
      candidates: 0,
      linked_at: '2026-01-01T00:00:00Z',
      registry: null,
    }
    const high: NpiLink = { ...none, npi: '1234567893', level: 'high', candidates: 3 }
    expect(bestLink([none])).toBeNull()
    expect(bestLink([high, { ...high, npi: '1234567801', level: 'potential' }])).toBe(high)
  })
})

describe('npiPortalUrl', () => {
  it("is the portal's provider page for the NPI", () => {
    expect(npiPortalUrl('1234567893')).toBe(
      'https://npiregistry.cms.hhs.gov/provider-view/1234567893',
    )
  })
})
