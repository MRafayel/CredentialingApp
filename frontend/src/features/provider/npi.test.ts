import { describe, expect, it } from 'vitest'
import type { NpiSuggestion, Verification } from '../../lib/api.ts'
import {
  candidatesLine,
  lookupDraft,
  npiShape,
  reasonWords,
  restsOnLive,
  splitSuggestions,
} from './npi.ts'

const verification = (details: Record<string, string>): Verification =>
  ({
    id: 'v',
    provider_id: 'p',
    credential_type: 'npi',
    source_id: 'nppes_monthly',
    outcome: 'completed',
    finding: 'record_found',
    match: null,
    summary: '',
    details,
    provenance: {
      source_id: 'nppes_monthly',
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
    changed_from: null,
  }) as Verification

const suggestion = (npi: string | null): NpiSuggestion => ({
  npi,
  level: npi ? 'potential' : 'none',
  evidence: [],
  explanation: npi ? 'Potential match' : 'No match: no candidate with this name.',
  registry: null,
})

describe('npiShape', () => {
  it('needs an NPI and a verification of it to read as verified', () => {
    const active = verification({ npi_status: 'active' })
    expect(npiShape(null, active)).toEqual({ kind: 'missing' })
    expect(npiShape('1234567893', null)).toEqual({ kind: 'unverified', npi: '1234567893' })
    expect(npiShape('1234567893', active)).toEqual({
      kind: 'verified',
      npi: '1234567893',
      result: active,
    })
  })

  it('does not take a "No NPI on file" result for a verification of the NPI set since', () => {
    expect(npiShape('1234567893', verification({ reason: 'no_npi' })).kind).toBe('unverified')
  })
})

describe('suggestion words', () => {
  it('counts the candidates, and says "more than 50" for the backend 51', () => {
    expect(candidatesLine(1)).toBe('1 candidate in the registry')
    expect(candidatesLine(3)).toBe('3 candidates in the registry')
    expect(candidatesLine(51)).toBe('More than 50 candidates in the registry')
  })

  it('keeps the reason of a row linked to nothing as a short phrase', () => {
    expect(reasonWords('No match: no candidate with this name.')).toBe(
      'No candidate with this name',
    )
    expect(reasonWords('No match: more than 50 namesakes.')).toBe('More than 50 namesakes')
    expect(reasonWords('The registry mirror is not loaded')).toBe(
      'The registry mirror is not loaded',
    )
  })

  it('splits the NPIs to weigh from the rows that say why there is none', () => {
    const { candidates, reasons } = splitSuggestions([
      suggestion('1234567893'),
      suggestion(null),
      suggestion('0000000001'),
    ])
    expect(candidates.map((row) => row.npi)).toEqual(['1234567893', '0000000001'])
    expect(reasons).toHaveLength(1)
  })

  it('names the live registry apart from the files', () => {
    expect(restsOnLive('nppes_api')).toBe(true)
    expect(restsOnLive('nppes_monthly')).toBe(false)
  })
})

describe('lookupDraft', () => {
  it('searches a person as "Last, First" and an organization by its name', () => {
    const person = { last: 'Sampleperson', first: 'Avery', middle: 'J', suffix: null }
    expect(lookupDraft({ person_name: person, organization_name: null })).toBe(
      'Sampleperson, Avery',
    )
    expect(lookupDraft({ person_name: { ...person, first: '' }, organization_name: null })).toBe(
      'Sampleperson',
    )
    expect(lookupDraft({ person_name: null, organization_name: 'Sampleville Care' })).toBe(
      'Sampleville Care',
    )
  })
})
