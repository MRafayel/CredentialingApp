import { describe, expect, it } from 'vitest'
import type { DatasetLoad } from '../../lib/api.ts'
import { durationSeconds, loadLine, loadSeconds, secondsText } from './load.ts'

const LOAD: DatasetLoad = {
  mode: 'incremental',
  scanned: 9_891_116,
  added: 24,
  changed: 6,
  unchanged: 9_891_086,
  outdated: 0,
  missing: 0,
  deactivated: 0,
  duplicates: 0,
  phases: { download: 'PT1M19.5S', scan: 'PT26.5S', read: 'PT7M28S' },
  cpu_time: 'PT4M7S',
}

describe('durationSeconds', () => {
  it('reads the ISO 8601 durations the backend sends', () => {
    expect(durationSeconds('PT0.039868S')).toBeCloseTo(0.039868)
    expect(durationSeconds('PT1M19.748892S')).toBeCloseTo(79.748892)
    expect(durationSeconds('PT2H')).toBe(7200)
    expect(durationSeconds('P1DT1S')).toBe(86_401)
    expect(durationSeconds('P7D')).toBe(604_800)
  })

  it('refuses what is not a duration', () => {
    expect(durationSeconds('')).toBeNull()
    expect(durationSeconds('P')).toBeNull()
    expect(durationSeconds('PT')).toBeNull()
    expect(durationSeconds('P1M')).toBeNull()
    expect(durationSeconds('12 s')).toBeNull()
  })
})

describe('loadLine', () => {
  it('sums the phases', () => {
    expect(loadSeconds(LOAD)).toBe(554)
    expect(loadSeconds({ phases: { a: 'PT1S', b: 'nonsense' } })).toBe(1)
    expect(loadSeconds({ phases: {} })).toBe(0)
  })

  it('writes whole seconds from ten up and one decimal below', () => {
    expect(secondsText(554.4)).toBe('554 s')
    expect(secondsText(3.24)).toBe('3.2 s')
    expect(secondsText(0)).toBe('0 s')
  })

  it('names the mode and the counts that are not zero', () => {
    const unchanged = new Intl.NumberFormat().format(9_891_086)
    expect(loadLine(LOAD)).toBe(
      `Last incremental load: 24 added · 6 changed · ${unchanged} unchanged · 554 s`,
    )
    expect(
      loadLine({
        ...LOAD,
        mode: 'deactivations',
        added: 0,
        changed: 0,
        unchanged: 0,
        deactivated: 6,
        outdated: 1,
        phases: { apply: 'PT2.5S' },
      }),
    ).toBe('Last deactivation load: 1 outdated · 6 deactivated · 2.5 s')
  })

  it('says so when a load counted nothing', () => {
    expect(
      loadLine({ ...LOAD, mode: 'full', added: 0, changed: 0, unchanged: 0, phases: {} }),
    ).toBe('Last full load: no rows · 0 s')
  })
})
