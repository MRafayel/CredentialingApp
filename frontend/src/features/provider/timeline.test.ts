import { describe, expect, it } from 'vitest'
import type { Verification } from '../../lib/api.ts'
import { npiStepTitle, timelineEntries } from './timeline.ts'

const result = (overrides: Partial<Verification>): Verification =>
  ({
    id: 'v',
    provider_id: 'p',
    credential_type: 'exclusion',
    source_id: 'oig_leie',
    outcome: 'completed',
    finding: 'no_record',
    match: null,
    summary: '',
    details: {},
    provenance: {
      source_id: 'oig_leie',
      source_url: 'https://example.test',
      method: 'bulk_dataset',
      verified_at: '2026-01-01T00:00:00Z',
      source_updated_at: null,
      snapshot_id: null,
      raw_record_ref: null,
    },
    freshness: 'cached',
    created_at: '2026-01-01T00:00:00Z',
    matched_records: [],
    ...overrides,
  }) as Verification

describe('timelineEntries', () => {
  it('keeps an NPI verification out of the check runs, as its own step, newest first', () => {
    const run = '2026-01-02T00:00:00.000Z'
    const entries = timelineEntries([
      result({ id: 'oig', created_at: run }),
      result({ id: 'npi-old', credential_type: 'npi', created_at: '2026-01-01T00:00:00Z' }),
      result({ id: 'ny', source_id: 'ny_omig', created_at: run }),
      result({ id: 'npi-new', credential_type: 'npi', created_at: '2026-01-03T00:00:00Z' }),
    ])
    expect(entries.map((entry) => entry.kind)).toEqual(['npi', 'run', 'npi'])
    const [newest, checked, oldest] = entries
    expect(newest?.kind === 'npi' && newest.result.id).toBe('npi-new')
    expect(checked?.kind === 'run' && checked.results.map((item) => item.id)).toEqual(['ny', 'oig'])
    expect(oldest?.kind === 'npi' && oldest.at).toBe('2026-01-01T00:00:00.000Z')
  })

  it('puts a run before an NPI step stored at the same moment, and handles no history', () => {
    const at = '2026-01-02T00:00:00Z'
    const entries = timelineEntries([
      result({ id: 'npi', credential_type: 'npi', created_at: at }),
      result({ id: 'oig', created_at: at }),
    ])
    expect(entries.map((entry) => entry.kind)).toEqual(['run', 'npi'])
    expect(timelineEntries([])).toEqual([])
  })
})

describe('npiStepTitle', () => {
  it('names each registry status and the checks that could not run', () => {
    const title = (details: Record<string, string>) => npiStepTitle({ details })
    expect(title({ npi_status: 'active' })).toBe('NPI verified: Active')
    expect(title({ npi_status: 'deactivated' })).toBe('NPI deactivated in NPPES')
    expect(title({ npi_status: 'not_found' })).toBe('NPI not in NPPES')
    expect(title({ npi_status: 'mirror_only_active' })).toBe('NPI needs review in NPPES')
    expect(title({ reason: 'no_npi' })).toBe('No NPI on file')
    expect(title({ live_check_outcome: 'blocked' })).toBe('NPI check could not run')
  })
})
