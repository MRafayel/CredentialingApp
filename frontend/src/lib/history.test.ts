import { describe, expect, it } from 'vitest'
import type { Verification } from './api.ts'
import { changeLine, checkRuns } from './history.ts'

let counter = 0

function result(overrides: Partial<Verification> & Pick<Verification, 'source_id'>): Verification {
  counter += 1
  return {
    id: `00000000-0000-0000-0000-${String(counter).padStart(12, '0')}`,
    provider_id: '00000000-0000-0000-0000-0000000000aa',
    credential_type: 'exclusion',
    outcome: 'completed',
    finding: 'no_record',
    match: null,
    summary: 'No record in Sample List',
    details: {},
    provenance: {
      source_id: overrides.source_id,
      source_url: 'https://example.test/list',
      method: 'bulk_dataset',
      verified_at: '2026-10-26T15:00:00Z',
      source_updated_at: '2026-10-09T00:00:00Z',
      snapshot_id: null,
      raw_record_ref: null,
    },
    freshness: 'cached',
    created_at: '2026-10-26T15:00:00Z',
    matched_records: [],
    changed_from: null,
    ...overrides,
  }
}

describe('checkRuns', () => {
  it('groups results stored together into one run, newest first', () => {
    const first = [
      result({ source_id: 'b', created_at: '2026-10-01T10:00:00.000Z' }),
      result({ source_id: 'a', created_at: '2026-10-01T10:00:00Z' }),
    ]
    const second = [
      result({ source_id: 'a', finding: 'ambiguous', created_at: '2026-10-02T10:00:00Z' }),
    ]

    const runs = checkRuns([...first, ...second])

    expect(runs.map((run) => run.at)).toEqual([
      '2026-10-02T10:00:00.000Z',
      '2026-10-01T10:00:00.000Z',
    ])
    expect(runs[0]?.results).toEqual(second)
    expect(runs[1]?.results.map((r) => r.source_id)).toEqual(['a', 'b'])
  })

  it('is empty without history', () => {
    expect(checkRuns([])).toEqual([])
  })
})

const change = (overrides: Partial<NonNullable<Verification['changed_from']>> = {}) => ({
  outcome: 'completed' as const,
  finding: 'no_record' as const,
  record_status: null,
  registry_status: null,
  verified_at: '2026-10-01T10:00:00Z',
  ...overrides,
})

describe('changeLine', () => {
  it('names what the result said before, in its own words', () => {
    expect(changeLine({ changed_from: change() })).toBe('changed from No record')
    expect(
      changeLine({
        changed_from: change({ finding: 'record_found', record_status: 'active' }),
      }),
    ).toBe('changed from Record found')
    expect(
      changeLine({
        changed_from: change({ finding: 'record_found', record_status: 'ended' }),
      }),
    ).toBe('changed from Previously listed')
    expect(changeLine({ changed_from: change({ finding: null, registry_status: 'active' }) })).toBe(
      'changed from Active',
    )
  })

  it('says nothing on a result that did not change', () => {
    expect(changeLine({ changed_from: null })).toBeNull()
  })
})
