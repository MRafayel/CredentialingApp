import { describe, expect, it } from 'vitest'
import type { ProviderVerdict, StatusSummary } from './api.ts'
import {
  filterAttention,
  filterChips,
  needingYou,
  parseFilter,
  providerSteps,
  rosterLine,
} from './monitoring.ts'

const summary = (counts: Record<string, number>, total: number): StatusSummary => ({
  recheck_interval_days: 15,
  counts: {
    listed: 0,
    flagged: 0,
    needs_review: 0,
    could_not_check: 0,
    changed: 0,
    npi_deactivated: 0,
    npi_uncertain: 0,
    npi_missing: 0,
    not_checked: 0,
    none: 0,
    ...counts,
  },
  total,
})

describe('rosterLine', () => {
  it('counts every reason for attention, never the quiet ones', () => {
    const roster = summary({ listed: 1, npi_missing: 2, none: 125 }, 128)
    expect(needingYou(roster)).toBe(3)
    expect(rosterLine(roster)).toEqual({ lead: '3 need you', total: '128 providers' })
  })

  it('says one provider needs you, and nothing when nobody does', () => {
    expect(rosterLine(summary({ changed: 1 }, 1)).lead).toBe('1 needs you')
    expect(rosterLine(summary({ none: 4 }, 4))).toEqual({
      lead: 'Nothing needs you',
      total: '4 providers',
    })
  })
})

describe('filters', () => {
  it('reads the URL value, falling back to "Needs you"', () => {
    expect(parseFilter(null)).toBe('needs_you')
    expect(parseFilter('all')).toBe('all')
    expect(parseFilter('listed')).toBe('listed')
    expect(parseFilter('none')).toBe('needs_you')
    expect(parseFilter('anything')).toBe('needs_you')
  })

  it('asks the API for any reason, one reason, or everyone', () => {
    expect(filterAttention('needs_you')).toBe('any')
    expect(filterAttention('all')).toBeUndefined()
    expect(filterAttention('npi_missing')).toBe('npi_missing')
  })

  it('shows "Needs you", the reasons with providers most serious first, then "All"', () => {
    const chips = filterChips(summary({ npi_missing: 7, listed: 5, none: 1 }, 13), 'needs_you')
    expect(chips.map((chip) => [chip.word, chip.count])).toEqual([
      ['Needs you', 12],
      ['Listed', 5],
      ['Needs NPI', 7],
      ['All', 13],
    ])
    expect(chips[1]?.label?.tone).toBe('listed')
  })

  it('keeps the chip in use even when its count is 0', () => {
    const chips = filterChips(summary({ listed: 1 }, 1), 'changed')
    expect(chips.map((chip) => chip.filter)).toEqual(['needs_you', 'listed', 'changed', 'all'])
  })
})

const verdict = (
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

const UTC = {
  locale: 'en-US',
  timeZone: 'UTC',
  now: new Date('2026-11-11T12:00:00Z'),
  intervalDays: 15,
}

describe('providerSteps', () => {
  it('reads a monitored, clear provider as three done steps', () => {
    const steps = providerSteps(
      {
        identity: 'npi_active',
        last_checked_at: '2026-11-10T10:00:00Z',
        next_check_at: '2026-11-25T10:00:00Z',
      },
      verdict({ verdict: 'no_records', sources_checked: 29 }),
      UTC,
    )
    expect(steps.map((step) => [step.title, step.line, step.state, step.action])).toEqual([
      ['Identity', 'NPI confirmed · active', 'done', null],
      ['Screening', 'No records in 29 lists', 'done', null],
      ['Monitoring', 'Rechecked every 15 days · next Nov 25, 2026', 'done', null],
    ])
  })

  it('waits on the NPI for a new provider; the check is the hero action, not a step', () => {
    const steps = providerSteps(
      { identity: 'npi_missing', last_checked_at: null, next_check_at: null },
      verdict({ verdict: 'not_checked' }),
      UTC,
    )
    expect(steps.map((step) => [step.line, step.state, step.action])).toEqual([
      ['Needs NPI', 'todo', 'find_npi'],
      ['Not checked', 'todo', null],
      ['Starts after the first check', 'waiting', null],
    ])
  })

  it('names each screening answer with its figure and its tone', () => {
    const line = (v: ProviderVerdict) =>
      providerSteps(
        { identity: 'not_expected', last_checked_at: null, next_check_at: null },
        v,
        UTC,
      )[1]
    expect(line(verdict({ verdict: 'listed', listed_in: ['oig_leie'] }))).toMatchObject({
      line: 'Listed in 1 list',
      state: 'trouble',
      tone: 'listed',
      action: null,
    })
    expect(line(verdict({ verdict: 'flagged', flagged_in: ['a', 'b'] }))).toMatchObject({
      line: 'Flagged in 2 lists',
      tone: 'flagged',
    })
    expect(line(verdict({ verdict: 'needs_review', review_in: ['a', 'b'] }))).toMatchObject({
      line: 'Review a match in 2 lists',
      tone: 'review',
    })
    expect(line(verdict({ verdict: 'could_not_check', unchecked: ['a', 'b', 'c'] }))).toMatchObject(
      { line: 'Could not check 3 lists', tone: 'blocked', action: null },
    )
  })

  it('marks NPI trouble in its own tone and leads to the NPI', () => {
    const identity = (
      value: 'npi_deactivated' | 'npi_uncertain' | 'npi_unverified' | 'not_expected',
    ) =>
      providerSteps(
        { identity: value, last_checked_at: null, next_check_at: null },
        verdict({ verdict: 'not_checked' }),
        UTC,
      )[0]
    expect(identity('npi_deactivated')).toMatchObject({
      line: 'NPI deactivated',
      state: 'trouble',
      tone: 'listed',
      action: 'review_npi',
    })
    expect(identity('npi_uncertain')).toMatchObject({ state: 'trouble', tone: 'review' })
    expect(identity('npi_unverified')).toMatchObject({ state: 'todo', action: 'verify_npi' })
    expect(identity('not_expected')).toMatchObject({ line: 'Not expected', state: 'done' })
  })

  it('starts monitoring with the first screening, not with an NPI verification', () => {
    const [, , monitoring] = providerSteps(
      { identity: 'npi_active', last_checked_at: '2026-11-10T10:00:00Z', next_check_at: null },
      verdict({ verdict: 'not_checked' }),
      UTC,
    )
    expect(monitoring).toMatchObject({ line: 'Starts after the first check', state: 'waiting' })
  })

  it('waits on a due recheck, never with a tick: no date, or a date passed', () => {
    const due = (next: string | null) =>
      providerSteps(
        { identity: 'npi_active', last_checked_at: '2026-10-20T10:00:00Z', next_check_at: next },
        verdict({ verdict: 'no_records', sources_checked: 3 }),
        UTC,
      )
    for (const next of [null, '2026-11-04T10:00:00Z']) {
      const steps = due(next)
      expect(steps[2]).toMatchObject({
        line: 'Due now · rechecked automatically',
        state: 'todo',
        action: null,
      })
    }
  })

  it('takes the interval from the backend', () => {
    const [, , monitoring] = providerSteps(
      {
        identity: 'npi_active',
        last_checked_at: '2026-11-10T10:00:00Z',
        next_check_at: '2026-12-10T10:00:00Z',
      },
      verdict({ verdict: 'no_records', sources_checked: 3 }),
      { ...UTC, intervalDays: 30 },
    )
    expect(monitoring?.line).toBe('Rechecked every 30 days · next Dec 10, 2026')
  })
})
