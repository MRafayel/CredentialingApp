import { describe, expect, it } from 'vitest'
import {
  isNameQuery,
  linkReason,
  liveDraft,
  liveQuery,
  looksLikeOrganization,
  summaryFacts,
} from './npiSearch.ts'

describe('isNameQuery', () => {
  it('takes names and leaves NPIs and licence numbers out', () => {
    expect(isNameQuery('Testperson, Avery')).toBe(true)
    expect(isNameQuery('Avery Testperson')).toBe(true)
    expect(isNameQuery('Acme Pharmacy Inc')).toBe(true)
    expect(isNameQuery('1234567893')).toBe(false)
    expect(isNameQuery('00048309')).toBe(false)
    expect(isNameQuery('MD123456')).toBe(false)
    expect(isNameQuery('A')).toBe(false)
  })
})

describe('liveDraft', () => {
  it('splits "Last, First" at the comma', () => {
    expect(liveDraft(' Testperson ,  Avery J ')).toMatchObject({
      kind: 'individual',
      last: 'Testperson',
      first: 'Avery J',
      organization: '',
    })
  })

  it('puts the last word of "First Last" in the last name', () => {
    expect(liveDraft('Avery  J Testperson')).toMatchObject({
      kind: 'individual',
      last: 'Testperson',
      first: 'Avery J',
    })
    expect(liveDraft('Testperson')).toMatchObject({ last: 'Testperson', first: '' })
  })

  it('fills the organization for an organization-looking query', () => {
    expect(looksLikeOrganization('Acme Pharmacy, Inc.')).toBe(true)
    expect(looksLikeOrganization('Testperson, Avery')).toBe(false)
    expect(liveDraft('Acme Pharmacy, Inc.')).toMatchObject({
      kind: 'organization',
      organization: 'Acme Pharmacy, Inc.',
      last: '',
      first: '',
    })
  })

  it('starts blank for nothing', () => {
    expect(liveDraft('  ')).toMatchObject({ last: '', first: '', organization: '' })
  })
})

describe('liveQuery', () => {
  const draft = {
    kind: 'individual' as const,
    last: 'Testperson',
    first: ' ',
    organization: 'Acme',
    state: 'TX',
    city: '',
  }

  it('sends the chosen kind’s fields and leaves blanks out', () => {
    expect(liveQuery(draft, 50)).toEqual({
      live: true,
      entity_kind: 'individual',
      last_name: 'Testperson',
      first_name: undefined,
      organization_name: undefined,
      state: 'TX',
      city: undefined,
      limit: 50,
    })
    expect(liveQuery({ ...draft, kind: 'organization' }, 50)).toMatchObject({
      entity_kind: 'organization',
      last_name: undefined,
      organization_name: 'Acme',
    })
  })
})

describe('linkReason', () => {
  it('drops the lead and the period', () => {
    expect(linkReason('No match: no candidate with this name.')).toBe('No candidate with this name')
    expect(linkReason('No match: more than 50 namesakes.')).toBe('More than 50 namesakes')
    expect(linkReason('Nothing to link')).toBe('Nothing to link')
  })
})

describe('summaryFacts', () => {
  it('names the kind, credential and practice place, leaving out what is blank', () => {
    expect(
      summaryFacts({
        entity_kind: 'individual',
        credential: 'MD',
        practice_city: 'Sampleville',
        practice_state: 'TX',
      }),
    ).toEqual(['Individual', 'MD', 'Sampleville, TX'])
    expect(
      summaryFacts({
        entity_kind: 'unknown',
        credential: null,
        practice_city: null,
        practice_state: null,
      }),
    ).toEqual([])
  })
})
