import { describe, expect, it } from 'vitest'
import type { ProviderVerdict } from './api.ts'
import { freshnessLabel, outcomeWord, resultLabel, verdictLabel } from './labels.ts'

const figures = (
  overrides: Partial<ProviderVerdict> & Pick<ProviderVerdict, 'verdict'>,
): ProviderVerdict => ({
  sources_checked: 0,
  listed_in: [],
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
