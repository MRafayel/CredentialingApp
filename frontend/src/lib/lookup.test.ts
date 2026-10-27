import { describe, expect, it } from 'vitest'
import type { LookupRecord, SearchedSource } from './api.ts'
import {
  actionLabel,
  draftFromRecord,
  isSearchable,
  listAges,
  noMatchesLine,
  placeText,
  searchedCount,
  searchedLine,
} from './lookup.ts'

const UTC = { locale: 'en-US', timeZone: 'UTC' }

const federal: SearchedSource = {
  source_id: 'fake_federal',
  display_name: 'Fake Federal List',
  list_last_updated: '2026-10-09T12:51:03Z',
  fetched_at: '2026-10-25T13:12:41Z',
}
const state: SearchedSource = {
  source_id: 'fake_state',
  display_name: 'Fake State List',
  list_last_updated: null,
  fetched_at: '2026-10-26T08:12:51Z',
}
const never: SearchedSource = {
  source_id: 'fake_never',
  display_name: 'Fake Unloaded List',
  list_last_updated: null,
  fetched_at: null,
}

const record: LookupRecord = {
  id: '00000000-0000-0000-0000-0000000000aa',
  source_id: 'fake_state',
  entity_kind: 'organization',
  person_name: null,
  organization_name: '7 STAR SAMPLEVILLE PHARMACY AND DELI INC',
  aliases: [],
  npi: '1000000014',
  action: 'termination',
  action_raw: null,
  effective_date: '2009-02-03',
  end_date: null,
  license: { number: '00012345', state: 'NY', kind: null },
  place: null,
  raw_provider_type: 'Pharmacy',
}

describe('isSearchable', () => {
  it('needs two characters after trimming', () => {
    expect(isSearchable('')).toBe(false)
    expect(isSearchable('  a ')).toBe(false)
    expect(isSearchable('ab')).toBe(true)
  })
})

describe('actionLabel', () => {
  it('shows restrictive actions in the listed tone', () => {
    for (const action of [
      'exclusion',
      'termination',
      'suspension',
      'debarment',
      'preclusion',
      'sanction',
    ] as const) {
      expect(actionLabel(action).tone).toBe('listed')
    }
    expect(actionLabel('termination').word).toBe('Termination')
  })

  it('keeps reinstatements and unknown actions apart from findings', () => {
    expect(actionLabel('reinstatement')).toEqual({ tone: 'neutral', word: 'Reinstatement' })
    expect(actionLabel('other')).toEqual({ tone: 'review', word: 'Other action' })
  })
})

describe('list ages', () => {
  it("uses the source's own date, then the fetch date, and says when a list was never loaded", () => {
    expect(listAges([federal, state, never]).map((age) => [age.kind, age.at])).toEqual([
      ['updated', federal.list_last_updated],
      ['fetched', state.fetched_at],
      ['not_loaded', null],
    ])
  })

  it('counts only loaded lists as searched', () => {
    expect(searchedCount([federal, state, never])).toBe(2)
    expect(searchedLine([federal, state, never], UTC)).toBe(
      'Searched 2 lists · Fake Federal List updated Oct 9, 2026 · Fake State List fetched Oct 26, 2026 · Fake Unloaded List not loaded, not searched',
    )
    expect(noMatchesLine([federal])).toBe('No matches in 1 list')
  })
})

describe('placeText', () => {
  it('joins what is there', () => {
    expect(placeText({ place: { city: 'Sampleville', state: 'NY' } })).toBe('Sampleville, NY')
    expect(placeText({ place: { city: null, state: 'NY' } })).toBe('NY')
    expect(placeText({ place: null })).toBeNull()
  })
})

describe('draftFromRecord', () => {
  it('prefills an organization with its NPI and licence', () => {
    expect(draftFromRecord(record)).toEqual({
      kind: 'organization',
      last: '',
      first: '',
      middle: '',
      suffix: '',
      organization: '7 STAR SAMPLEVILLE PHARMACY AND DELI INC',
      npi: '1000000014',
      licences: [{ number: '00012345', state: 'NY' }],
    })
  })

  it('prefills a person by name parts', () => {
    const person = draftFromRecord({
      ...record,
      entity_kind: 'individual',
      organization_name: null,
      person_name: { last: 'Testperson', first: 'Avery', middle: 'J', suffix: null },
      npi: null,
      license: null,
    })
    expect(person).toMatchObject({
      kind: 'individual',
      last: 'Testperson',
      first: 'Avery',
      middle: 'J',
      suffix: '',
      organization: '',
      npi: '',
      licences: [],
    })
  })
})
