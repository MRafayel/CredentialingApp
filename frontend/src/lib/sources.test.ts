import { describe, expect, it } from 'vitest'
import { healthLabel, sourcesSummary } from './sources.ts'

describe('sourcesSummary', () => {
  it('counts like the command line', () => {
    expect(sourcesSummary([{ health: 'ok' }, { health: 'ok' }])).toBe(
      '2 sources · 2 ok · 0 overdue · 0 failing',
    )
    expect(sourcesSummary([{ health: 'failing' }, { health: 'never_fetched' }])).toBe(
      '2 sources · 0 ok · 0 overdue · 1 failing · 1 never fetched',
    )
    expect(sourcesSummary([{ health: 'overdue' }])).toBe('1 source · 0 ok · 1 overdue · 0 failing')
  })
})

describe('healthLabel', () => {
  it('pairs each health with its colour', () => {
    expect(healthLabel('ok').tone).toBe('clear')
    expect(healthLabel('overdue').tone).toBe('review')
    expect(healthLabel('failing').tone).toBe('listed')
    expect(healthLabel('never_fetched')).toEqual({ tone: 'blocked', word: 'Never fetched' })
  })
})
