import { describe, expect, it } from 'vitest'
import type { NpiRecord, Provider } from '../../lib/api.ts'
import { addedLicences, addedPlace, addedTaxonomies, registryNameIfDifferent } from './facts.ts'

type Taxonomy = NpiRecord['taxonomies'][number]

const taxonomy = (overrides: Partial<Taxonomy>): Taxonomy => ({
  code: '207Q00000X',
  primary: false,
  license_number: null,
  license_state: null,
  group: null,
  ...overrides,
})

const person = (last: string, first: string, middle: string | null = null) => ({
  person_name: { last, first, middle, suffix: null },
  organization_name: null,
})

describe('registryNameIfDifferent', () => {
  it('gives the registry name only when it reads differently, ignoring case', () => {
    expect(
      registryNameIfDifferent(person('Sampleperson', 'Blair'), person('SAMPLEPERSON', 'BLAIR')),
    ).toBeNull()
    expect(
      registryNameIfDifferent(
        person('Sampleperson', 'Blair'),
        person('SAMPLEPERSON', 'BLAIR', 'Q'),
      ),
    ).toBe('SAMPLEPERSON, BLAIR Q')
  })

  it('compares organizations too, and says nothing when a side has no name', () => {
    const roster = { person_name: null, organization_name: 'Sampleville Care Group' }
    expect(
      registryNameIfDifferent(roster, {
        person_name: null,
        organization_name: 'SAMPLEVILLE CARE GROUP',
      }),
    ).toBeNull()
    expect(
      registryNameIfDifferent(roster, {
        person_name: null,
        organization_name: 'SAMPLEVILLE CARE LLC',
      }),
    ).toBe('SAMPLEVILLE CARE LLC')
    expect(
      registryNameIfDifferent(roster, { person_name: null, organization_name: null }),
    ).toBeNull()
  })
})

describe('addedLicences', () => {
  const provider: Pick<Provider, 'licenses'> = {
    licenses: [{ state: 'NY', number: '012-345', kind: null }],
  }

  it('adds only the licences the roster lacks, ignoring case and punctuation', () => {
    const added = addedLicences(provider, {
      taxonomies: [
        taxonomy({ license_state: 'ny', license_number: '012345' }),
        taxonomy({ license_state: 'NJ', license_number: 'ab 77', primary: true }),
        taxonomy({ license_state: 'NY', license_number: '999' }),
      ],
    })
    expect(added).toEqual([
      { state: 'NJ', number: 'ab 77' },
      { state: 'NY', number: '999' },
    ])
  })

  it('keeps a number from another state apart, skips half-given licences and repeats', () => {
    const added = addedLicences(provider, {
      taxonomies: [
        taxonomy({ license_state: 'CT', license_number: '012345' }),
        taxonomy({ code: '363L00000X', license_state: 'CT', license_number: '012-345' }),
        taxonomy({ license_state: null, license_number: '5' }),
        taxonomy({ license_state: 'CT', license_number: null }),
      ],
    })
    expect(added).toEqual([{ state: 'CT', number: '012345' }])
    expect(addedLicences({ licenses: [] }, { taxonomies: [] })).toEqual([])
  })
})

describe('addedPlace', () => {
  const address = (city: string | null, state: string | null) => ({
    line1: '1 Example Way',
    line2: null,
    city,
    state,
    postal_code: null,
  })

  it('adds the practice place unless a roster address has its city and state', () => {
    const record = { practice_city: 'SAMPLEVILLE', practice_state: 'NY' }
    expect(addedPlace({ addresses: [] }, record)).toBe('SAMPLEVILLE, NY')
    expect(addedPlace({ addresses: [address('Sampleville', 'ny')] }, record)).toBeNull()
    expect(addedPlace({ addresses: [address('Sampleville', 'NJ')] }, record)).toBe(
      'SAMPLEVILLE, NY',
    )
    expect(addedPlace({ addresses: [] }, { practice_city: null, practice_state: null })).toBeNull()
  })
})

describe('addedTaxonomies', () => {
  it('adds the codes the roster lacks, primary first, each once', () => {
    const added = addedTaxonomies(
      { taxonomy_codes: ['207q00000x'] },
      {
        taxonomies: [
          taxonomy({ code: '207Q00000X', primary: true }),
          taxonomy({ code: '363L00000X', license_state: 'NY', license_number: '1' }),
          taxonomy({ code: '363L00000X', license_state: 'NY', license_number: '1' }),
          taxonomy({ code: '363L00000X', license_state: 'NJ', license_number: '2' }),
          taxonomy({ code: '261QM0801X', primary: true }),
        ],
      },
    )
    expect(added.map((item) => [item.code, item.license_state])).toEqual([
      ['261QM0801X', null],
      ['363L00000X', 'NY'],
      ['363L00000X', 'NJ'],
    ])
    expect(addedTaxonomies({ taxonomy_codes: [] }, { taxonomies: [] })).toEqual([])
  })
})
