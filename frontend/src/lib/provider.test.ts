import { describe, expect, it } from 'vitest'
import { displayName, expectedSources, providerStates } from './provider.ts'

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

describe('expectedSources', () => {
  const licences = [{ number: '1', state: 'NY', kind: null }]
  const addresses = [
    { line1: null, line2: null, city: 'Sampleville', state: 'NJ', postal_code: null },
  ]

  it('covers federal lists and the states of licences and addresses', () => {
    const sources = [
      { id: 'federal', jurisdictions: ['US'] },
      { id: 'ny', jurisdictions: ['NY'] },
      { id: 'ca', jurisdictions: ['CA'] },
    ]
    expect(providerStates({ licenses: licences, addresses })).toEqual(['NJ', 'NY'])
    expect(expectedSources(sources, { licenses: licences, addresses }).map((s) => s.id)).toEqual([
      'federal',
      'ny',
    ])
  })
})
