import { describe, expect, it } from 'vitest'
import {
  coveredStates,
  hasFetched,
  heat,
  heatScale,
  stateLists,
  stateName,
  totalExcluded,
} from './mapInfo.ts'
import type { SourceStatus } from '../../../lib/api.ts'

/** A completed fetch whose source states no list date of its own (as Kansas's does not). */
const FETCHED: SourceStatus['latest_completed'] = {
  id: '00000000-0000-4000-8000-000000000001',
  source_id: 'fake',
  fetched_at: '2026-10-09T12:00:00Z',
  outcome: 'completed',
  source_url: 'https://example.test/list.csv',
  content_hash: null,
  etag: null,
  last_modified: null,
  byte_size: null,
  row_count: null,
  note: null,
  load: null,
}

const SOURCES = [
  {
    source_id: 'federal',
    jurisdictions: ['US'],
    current_records: 84000,
    latest_completed: FETCHED,
  },
  { source_id: 'ny', jurisdictions: ['NY'], current_records: 9145, latest_completed: FETCHED },
  { source_id: 'pa', jurisdictions: ['PA'], current_records: 0, latest_completed: null },
  {
    source_id: 'regional',
    jurisdictions: ['NY', 'NJ'],
    current_records: 12,
    latest_completed: FETCHED,
  },
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

  it('is true for a loaded list that states no list date of its own', () => {
    expect(FETCHED?.last_modified).toBeNull()
    expect(
      hasFetched([{ jurisdictions: ['KS'], current_records: 40, latest_completed: FETCHED }], 'KS'),
    ).toBe(true)
  })

  it('is false for a list never fetched and for a state without a list', () => {
    expect(hasFetched(SOURCES, 'PA')).toBe(false)
    expect(hasFetched(SOURCES, 'TX')).toBe(false)
  })
})

describe('heat', () => {
  const HEAT = [
    { jurisdictions: ['US'], current_records: 84000, latest_completed: FETCHED },
    { jurisdictions: ['NY'], current_records: 9999, latest_completed: FETCHED },
    { jurisdictions: ['WA'], current_records: 99, latest_completed: FETCHED },
    { jurisdictions: ['VT'], current_records: 9, latest_completed: FETCHED },
    { jurisdictions: ['PA'], current_records: 0, latest_completed: null },
  ]

  it('spans the covered states with data, leaving out the federal list and unfetched lists', () => {
    expect(heatScale(HEAT)).toEqual({ min: 9, max: 9999 })
    expect(
      heatScale([{ jurisdictions: ['US'], current_records: 5, latest_completed: FETCHED }]),
    ).toBe(null)
  })

  it('places each state on a log scale from the fewest to the most excluded', () => {
    expect(heat(HEAT, 'VT')).toBe(0)
    expect(heat(HEAT, 'NY')).toBe(1)
    // ln(100) sits a third of the way from ln(10) to ln(10000).
    expect(heat(HEAT, 'WA')).toBeCloseTo(1 / 3, 10)
  })

  it('is null for a state without a list or without data, never a low value', () => {
    expect(heat(HEAT, 'TX')).toBeNull()
    expect(heat(HEAT, 'PA')).toBeNull()
    expect(heat([], 'NY')).toBeNull()
  })

  it('reads as the top of the scale when every state has the same total', () => {
    const same = HEAT.slice(1, 2)
    expect(heat(same, 'NY')).toBe(1)
  })

  it('adds a multi-state list to each state it names', () => {
    expect(heat(SOURCES, 'NY')).toBe(1)
    expect(heat(SOURCES, 'NJ')).toBe(0)
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
