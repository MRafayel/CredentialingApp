import { describe, expect, it } from 'vitest'
import { draftFromNpi } from './draft.ts'

const taxonomy = (code: string, number: string | null, state: string | null) => ({
  code,
  primary: false,
  license_number: number,
  license_state: state,
  group: null,
})

describe('draftFromNpi', () => {
  it('opens a person with the registry name, the NPI and each licence once', () => {
    expect(
      draftFromNpi({
        npi: '1234567893',
        entity_kind: 'individual',
        person_name: { last: 'Testperson', first: 'Avery', middle: 'J', suffix: null },
        organization_name: null,
        taxonomies: [
          taxonomy('207Q00000X', 'MD-0001', 'TX'),
          taxonomy('207R00000X', 'MD-0001', 'tx'),
          taxonomy('390200000X', null, 'TX'),
          taxonomy('174400000X', 'ZZ-1', 'Texas'),
        ],
      }),
    ).toEqual({
      kind: 'individual',
      last: 'Testperson',
      first: 'Avery',
      middle: 'J',
      suffix: '',
      organization: '',
      npi: '1234567893',
      licences: [{ number: 'MD-0001', state: 'TX' }],
    })
  })

  it('opens an organization, or a row with only an organization name, as one', () => {
    const organization = {
      npi: '1234567893',
      entity_kind: 'organization' as const,
      person_name: null,
      organization_name: 'Sampleville Care',
      taxonomies: [],
    }
    expect(draftFromNpi(organization)).toMatchObject({
      kind: 'organization',
      organization: 'Sampleville Care',
      last: '',
    })
    expect(draftFromNpi({ ...organization, entity_kind: 'unknown' })).toMatchObject({
      kind: 'organization',
    })
    expect(
      draftFromNpi({ ...organization, entity_kind: 'unknown', organization_name: null }),
    ).toMatchObject({ kind: 'individual', last: '', organization: '' })
  })
})
