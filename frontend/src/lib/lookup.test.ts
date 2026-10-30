import { describe, expect, it } from 'vitest'
import type { LookupRecord, SearchedSource } from './api.ts'
import {
  actionLabel,
  draftFromRecord,
  isSearchable,
  matchesLine,
  noMatchesLine,
  placeText,
  recordLabel,
  searchedCount,
  searchedLine,
  searchedScope,
} from './lookup.ts'

const federal: SearchedSource = {
  source_id: 'fake_federal',
  display_name: 'Fake Federal List',
  jurisdictions: ['US'],
  list_last_updated: '2026-10-09T12:51:03Z',
  fetched_at: '2026-10-25T13:12:41Z',
}
const state: SearchedSource = {
  source_id: 'fake_wa',
  display_name: 'Fake Washington List',
  jurisdictions: ['WA'],
  list_last_updated: null,
  fetched_at: '2026-10-26T08:12:51Z',
}
const older: SearchedSource = {
  source_id: 'fake_ny',
  display_name: 'Fake New York List',
  jurisdictions: ['NY'],
  list_last_updated: '2026-07-27T00:00:00Z',
  fetched_at: '2026-10-27T09:00:00Z',
}
const regional: SearchedSource = {
  source_id: 'fake_regional',
  display_name: 'Fake Regional List',
  jurisdictions: ['NY', 'NJ'],
  list_last_updated: '2026-10-09T03:00:00Z',
  fetched_at: '2026-10-27T09:00:00Z',
}
const never: SearchedSource = {
  source_id: 'fake_never',
  display_name: 'Fake Unloaded List',
  jurisdictions: ['IA'],
  list_last_updated: null,
  fetched_at: null,
}
const WASHINGTON = { code: 'WA', name: 'Washington' }

const record: LookupRecord = {
  id: '00000000-0000-0000-0000-0000000000aa',
  source_id: 'fake_state',
  entity_kind: 'organization',
  person_name: null,
  organization_name: '7 STAR SAMPLEVILLE PHARMACY AND DELI INC',
  aliases: [],
  related_parties: [],
  npi: '1000000014',
  related_npis: [],
  action: 'termination',
  action_raw: null,
  effective_date: '2009-02-03',
  end_date: null,
  license: { number: '00012345', state: 'NY', kind: null },
  place: null,
  raw_provider_type: 'Pharmacy',
  status: 'active',
}

describe('isSearchable', () => {
  it('needs two characters after trimming', () => {
    expect(isSearchable('')).toBe(false)
    expect(isSearchable('  a ')).toBe(false)
    expect(isSearchable('ab')).toBe(true)
  })
})

describe('recordLabel', () => {
  it('shows the action while the record stands, its status otherwise', () => {
    expect(recordLabel(record)).toEqual({ tone: 'listed', word: 'Termination' })
    expect(recordLabel({ ...record, status: 'ended' })).toEqual({
      tone: 'review',
      word: 'Previously listed',
    })
    expect(recordLabel({ ...record, status: 'removed' })).toEqual({
      tone: 'review',
      word: 'Removed from published list',
    })
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
    expect(actionLabel('disqualification')).toEqual({ tone: 'listed', word: 'Disqualification' })
  })
})

describe('searchedLine', () => {
  it('counts distinct states and the federal list, never naming lists', () => {
    expect(searchedLine([federal, state, older, regional])).toBe(
      'Searched 3 states and the federal list',
    )
  })

  it('says how many lists were not loaded', () => {
    expect(searchedLine([federal, regional, never])).toBe(
      'Searched 2 states and the federal list · 1 list not loaded',
    )
  })

  it('names several federal lists, and states or the federal list alone', () => {
    const second = { ...federal, source_id: 'fake_federal_2' }
    expect(searchedLine([federal, second])).toBe('Searched the federal lists')
    expect(searchedLine([state])).toBe('Searched 1 state')
  })

  it('never counts a list that was not loaded as searched', () => {
    expect(searchedLine([never])).toBe('Searched no lists · 1 list not loaded')
    expect(searchedScope([never, federal])).toEqual({
      where: 'Searched the federal list',
      notLoaded: 1,
    })
  })

  it('narrowed to a state, names its own list and the federal list', () => {
    expect(searchedLine([federal, state, older], WASHINGTON)).toBe(
      'Searched in Washington · its state list and the federal list',
    )
  })

  it('says when the state has no list of its own yet', () => {
    expect(searchedLine([federal, older], WASHINGTON)).toBe(
      'Searched in Washington · no state list yet, the federal list only',
    )
    expect(searchedLine([older], WASHINGTON)).toBe('Searched in Washington · no state list yet')
  })

  it("says when the state's own list was not loaded, without counting it twice", () => {
    const unloaded = { ...state, fetched_at: null }
    expect(searchedLine([federal, unloaded, never], WASHINGTON)).toBe(
      'Searched in Washington · its state list not loaded, the federal list only · 1 list not loaded',
    )
  })
})

describe('searchedCount', () => {
  it('counts only loaded lists as searched', () => {
    expect(searchedCount([federal, state, never])).toBe(2)
    expect(noMatchesLine([federal])).toBe('No matches in 1 list')
    expect(noMatchesLine([federal, state, never], 'Washington')).toBe('No matches in Washington')
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

describe('matchesLine', () => {
  it('counts the matches, and names the state a search was narrowed to', () => {
    expect(matchesLine(1, 25)).toBe('1 match')
    expect(matchesLine(3, 25, 'Washington')).toBe('3 matches in Washington')
    expect(matchesLine(3, 25, null)).toBe('3 matches')
  })

  it('asks for another word when the page is full', () => {
    expect(matchesLine(25, 25)).toBe('The best 25 matches; add a word to narrow them')
    expect(matchesLine(25, 25, 'Iowa')).toBe(
      'The best 25 matches in Iowa; add a word to narrow them',
    )
  })
})
