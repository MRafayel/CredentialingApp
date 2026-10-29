import { describe, expect, it } from 'vitest'
import { coveredStates, hasFetched, stateLists, stateName, totalExcluded } from './mapInfo.ts'

const FETCHED = '2026-10-09T12:00:00Z'

const SOURCES = [
  { source_id: 'federal', jurisdictions: ['US'], current_records: 84000, data_as_of: FETCHED },
  { source_id: 'ny', jurisdictions: ['NY'], current_records: 9145, data_as_of: FETCHED },
  { source_id: 'pa', jurisdictions: ['PA'], current_records: 0, data_as_of: null },
  { source_id: 'regional', jurisdictions: ['NY', 'NJ'], current_records: 12, data_as_of: FETCHED },
]

describe('stateLists', () => {
  it('finds every list naming the state', () => {
    expect(stateLists(SOURCES, 'NY').map((s) => s.source_id)).toEqual(['ny', 'regional'])
    expect(stateLists(SOURCES, 'NJ').map((s) => s.source_id)).toEqual(['regional'])
  })

  it('is empty for a state without a list', () => {
    expect(stateLists(SOURCES, 'TX')).toEqual([])
  })
})

describe('coveredStates', () => {
  it('counts each state once and leaves the federal list out', () => {
    expect([...coveredStates(SOURCES)].sort()).toEqual(['NJ', 'NY', 'PA'])
    expect(coveredStates([{ jurisdictions: ['US'] }]).size).toBe(0)
  })
})

describe('totalExcluded', () => {
  it('adds up the records of every list naming the state', () => {
    expect(totalExcluded(SOURCES, 'NY')).toBe(9157)
    expect(totalExcluded(SOURCES, 'NJ')).toBe(12)
  })

  it('is null, never 0, for a state without a list', () => {
    expect(totalExcluded(SOURCES, 'TX')).toBeNull()
    expect(totalExcluded([], 'NY')).toBeNull()
  })

  it('keeps a real zero for a state whose list is empty', () => {
    expect(totalExcluded(SOURCES, 'PA')).toBe(0)
  })
})

describe('hasFetched', () => {
  it('is true once any of the state lists has data', () => {
    expect(hasFetched(SOURCES, 'NY')).toBe(true)
  })

  it('is false for a list never fetched and for a state without a list', () => {
    expect(hasFetched(SOURCES, 'PA')).toBe(false)
    expect(hasFetched(SOURCES, 'TX')).toBe(false)
  })
})

describe('stateName', () => {
  it('names a state by its code', () => {
    expect(stateName('WA')).toBe('Washington')
    expect(stateName('DC')).toBe('District of Columbia')
  })

  it('falls back to the code for one the map does not draw', () => {
    expect(stateName('GU')).toBe('GU')
  })
})
