import { describe, expect, it } from 'vitest'
import type { ProviderVerdict } from './api.ts'
import {
  freshnessLabel,
  matchLevelLabel,
  npiStatusLabel,
  outcomeWord,
  recordStatusLabel,
  registryStatusLabel,
  resultLabel,
  verdictLabel,
} from './labels.ts'

const figures = (
  overrides: Partial<ProviderVerdict> & Pick<ProviderVerdict, 'verdict'>,
): ProviderVerdict => ({
  sources_checked: 0,
  listed_in: [],
  flagged_in: [],
  review_in: [],
  unchecked: [],
  oldest_list_updated_at: null,
  last_checked_at: null,
  results: [],
  ...overrides,
})

describe('verdictLabel', () => {
  it('names each verdict the backend gives, with the figure for it', () => {
    expect(
      verdictLabel(figures({ verdict: 'listed', sources_checked: 2, listed_in: ['ny_omig'] })),
    ).toEqual({ tone: 'listed', word: 'Listed', detail: 'in 1 list' })
    expect(
      verdictLabel(figures({ verdict: 'flagged', flagged_in: ['wa_hca', 'oig_leie'] })),
    ).toEqual({ tone: 'flagged', word: 'Flagged', detail: 'in 2 lists' })
    expect(
      verdictLabel(figures({ verdict: 'needs_review', review_in: ['ny_omig', 'oig_leie'] })),
    ).toEqual({ tone: 'review', word: 'Needs review', detail: 'in 2 lists' })
    expect(verdictLabel(figures({ verdict: 'could_not_check', unchecked: ['a', 'b'] }))).toEqual({
      tone: 'blocked',
      word: 'Could not check 2',
    })
    expect(verdictLabel(figures({ verdict: 'no_records', sources_checked: 2 }))).toEqual({
      tone: 'clear',
      word: 'No records',
      detail: 'in 2 lists',
    })
    expect(verdictLabel(figures({ verdict: 'not_checked' }))).toEqual({
      tone: 'neutral',
      word: 'Not checked',
    })
  })

  it('shows the verdict as given, even when the figures would suggest another', () => {
    // A listed verdict stays "Listed" whatever else the figures hold: the UI never re-decides.
    const label = verdictLabel(
      figures({ verdict: 'listed', listed_in: ['a'], unchecked: ['b'], review_in: ['c'] }),
    )
    expect(label.word).toBe('Listed')
  })
})

describe('resultLabel', () => {
  it('uses the outcome when the source could not be consulted', () => {
    expect(resultLabel({ outcome: 'blocked', finding: null })).toEqual({
      tone: 'blocked',
      word: 'Blocked',
    })
    expect(resultLabel({ outcome: 'completed', finding: 'record_found' }).tone).toBe('listed')
    expect(resultLabel({ outcome: 'completed', finding: 'ambiguous' }).word).toBe('Needs review')
    expect(resultLabel({ outcome: 'completed', finding: 'no_record' }).word).toBe('No record')
  })

  it('reads a found record that has ended as previously listed, in the review tone', () => {
    expect(
      resultLabel({
        outcome: 'completed',
        finding: 'record_found',
        details: { record_status: 'ended' },
      }),
    ).toEqual({ tone: 'review', word: 'Previously listed' })
    expect(
      resultLabel({
        outcome: 'completed',
        finding: 'record_found',
        details: { record_status: 'active' },
      }),
    ).toEqual({ tone: 'listed', word: 'Record found' })
    expect(
      resultLabel({
        outcome: 'completed',
        finding: 'ambiguous',
        details: { record_status: 'ended' },
      }).word,
    ).toBe('Needs review')
  })

  it('reads a related-party record as flagged, and an ended one as previously flagged', () => {
    expect(resultLabel({ outcome: 'completed', finding: 'related_party' })).toEqual({
      tone: 'flagged',
      word: 'Flagged',
    })
    expect(
      resultLabel({
        outcome: 'completed',
        finding: 'related_party',
        details: { record_status: 'ended' },
      }),
    ).toEqual({ tone: 'review', word: 'Previously flagged' })
  })

  it('reads a found or related-party record removed from its list in the review tone', () => {
    for (const finding of ['record_found', 'related_party'] as const) {
      expect(
        resultLabel({ outcome: 'completed', finding, details: { record_status: 'removed' } }),
      ).toEqual({ tone: 'review', word: 'Removed from published list' })
    }
  })
})

describe('outcomeWord', () => {
  it('names known outcomes and spells out unknown ones', () => {
    expect(outcomeWord('source_unavailable')).toBe('Source unavailable')
    expect(outcomeWord('rate_limited')).toBe('rate limited')
  })
})

describe('freshnessLabel', () => {
  it('keeps live, cached, stale and manual apart', () => {
    expect(freshnessLabel('live').word).toBe('Live')
    expect(freshnessLabel('cached').word).toBe('Cached')
    expect(freshnessLabel('stale')).toEqual({ tone: 'review', word: 'Stale' })
    expect(freshnessLabel('manual').word).toBe('Manually verified')
  })
})

describe('recordStatusLabel', () => {
  it('reads a standing record as listed and an ended one as previously listed', () => {
    expect(recordStatusLabel('active')).toEqual({ tone: 'listed', word: 'Listed' })
    expect(recordStatusLabel('ended')).toEqual({ tone: 'review', word: 'Previously listed' })
  })

  it('reads a record removed from its published list in the review tone', () => {
    expect(recordStatusLabel('removed')).toEqual({
      tone: 'review',
      word: 'Removed from published list',
    })
  })
})

describe('the NPI registry labels', () => {
  it('names an NPI verification by the registry status the backend put in its details', () => {
    const base = { outcome: 'completed' as const, finding: 'record_found' as const }
    expect(resultLabel({ ...base, details: { npi_status: 'active' } })).toEqual({
      tone: 'clear',
      word: 'Active',
    })
    expect(resultLabel({ ...base, details: { npi_status: 'deactivated' } })).toEqual({
      tone: 'listed',
      word: 'Deactivated',
    })
    expect(
      resultLabel({ ...base, finding: 'no_record', details: { npi_status: 'not_found' } }),
    ).toEqual({ tone: 'review', word: 'Not in NPPES' })
    expect(
      resultLabel({ ...base, finding: 'ambiguous', details: { npi_status: 'mirror_only_active' } }),
    ).toEqual({ tone: 'review', word: 'Needs review' })
    expect(
      resultLabel({ outcome: 'manual_required', finding: null, details: { reason: 'no_npi' } }),
    ).toEqual({ tone: 'review', word: 'No NPI on file' })
    // An exclusion result with no registry status reads as before.
    expect(resultLabel({ ...base, details: {} })).toEqual({ tone: 'listed', word: 'Record found' })
  })

  it('gives every registry status, NPI status and match level a tone and a word', () => {
    expect(registryStatusLabel('unknown')).toEqual({ tone: 'blocked', word: 'Could not check' })
    expect(npiStatusLabel('active')).toEqual({ tone: 'clear', word: 'Active' })
    expect(npiStatusLabel('deactivated')).toEqual({ tone: 'listed', word: 'Deactivated' })
    expect(matchLevelLabel('exact')).toEqual({ tone: 'clear', word: 'Exact' })
    expect(matchLevelLabel('high')).toEqual({ tone: 'clear', word: 'High' })
    expect(matchLevelLabel('potential')).toEqual({ tone: 'review', word: 'Potential' })
    expect(matchLevelLabel('ambiguous')).toEqual({ tone: 'review', word: 'Ambiguous' })
    expect(matchLevelLabel('none')).toEqual({ tone: 'neutral', word: 'No match' })
  })
})
