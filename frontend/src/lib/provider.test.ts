import { describe, expect, it } from 'vitest'
import { displayName, entityKindWord, providerStates } from './provider.ts'

const person = { last: 'Sampleperson', first: 'Avery', middle: 'J', suffix: null }

describe('displayName', () => {
  it('puts the last name first for a person', () => {
    expect(displayName({ person_name: person, organization_name: null })).toBe(
      'Sampleperson, Avery J',
    )
    expect(
      displayName({
        person_name: { last: 'Testperson', first: '', middle: null, suffix: null },
        organization_name: null,
      }),
    ).toBe('Testperson')
  })

  it('uses the organization name otherwise', () => {
    expect(displayName({ person_name: null, organization_name: 'Sampleville Care Group' })).toBe(
      'Sampleville Care Group',
    )
  })
})

describe('providerStates', () => {
  it('collects the states of licences and addresses, sorted and once each', () => {
    const licences = [
      { number: '1', state: 'NY', kind: null },
      { number: '2', state: 'NJ', kind: null },
    ]
    const addresses = [
      { line1: null, line2: null, city: 'Sampleville', state: 'NJ', postal_code: null },
      { line1: null, line2: null, city: 'Testtown', state: null, postal_code: null },
    ]
    expect(providerStates({ licenses: licences, addresses })).toEqual(['NJ', 'NY'])
  })
})

describe('entityKindWord', () => {
  it('names each kind, and says so when the source does not', () => {
    expect(entityKindWord('individual')).toBe('Individual')
    expect(entityKindWord('organization')).toBe('Organization')
    expect(entityKindWord('unknown')).toBe('Unknown kind')
  })
})
