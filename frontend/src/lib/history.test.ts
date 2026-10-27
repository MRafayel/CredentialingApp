import { describe, expect, it } from 'vitest'
import type { Verification } from './api.ts'
import { checkRuns } from './history.ts'

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
