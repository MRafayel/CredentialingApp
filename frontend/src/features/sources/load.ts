/**
 * What the last load of a dataset file did, in one line for the sources page: its mode, the
 * counts that are not zero and how long its phases took. Read from the backend's figures only.
 */
import type { DatasetLoad } from '../../lib/api.ts'

const DAY = 86_400
const HOUR = 3_600
const MINUTE = 60

/**
 * An ISO 8601 duration ("PT1M19.748892S", "P1DT2H") in seconds, or null when it is not one.
 * Years and months have no fixed length and are not accepted.
 */
export function durationSeconds(value: string): number | null {
  const match =
    /^P(?:(\d+(?:\.\d+)?)D)?(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?$/.exec(
      value,
    )
  if (!match || value === 'P' || value.endsWith('T')) return null
  const [, days, hours, minutes, seconds] = match
  return (
    Number(days ?? 0) * DAY +
    Number(hours ?? 0) * HOUR +
    Number(minutes ?? 0) * MINUTE +
    Number(seconds ?? 0)
  )
}

/** The sum of a load's phase durations, in seconds; a phase that is not a duration counts 0. */
export function loadSeconds(load: Pick<DatasetLoad, 'phases'>): number {
  return Object.values(load.phases).reduce((sum, phase) => sum + (durationSeconds(phase) ?? 0), 0)
}

/** "554 s", "3.2 s": whole seconds from ten up, one decimal below. */
export function secondsText(seconds: number): string {
  const format = new Intl.NumberFormat(undefined, {
    maximumFractionDigits: seconds < 10 ? 1 : 0,
  })
  return `${format.format(seconds)} s`
}

const MODE_WORDS: Record<DatasetLoad['mode'], string> = {
  full: 'Last full load',
  incremental: 'Last incremental load',
  deactivations: 'Last deactivation load',
}

/** The counts a load reports, in the order the loader works through them. */
const COUNTS = [
  ['added', 'added'],
  ['changed', 'changed'],
  ['unchanged', 'unchanged'],
  ['outdated', 'outdated'],
  ['missing', 'missing'],
  ['deactivated', 'deactivated'],
  ['duplicates', 'duplicates'],
] as const satisfies readonly (readonly [keyof DatasetLoad, string])[]

/**
 * "Last full load: 24 added · 6 changed · 9,891,086 unchanged · 554 s". Zero counts are left
 * out; a load that counted nothing says "no rows".
 */
export function loadLine(load: DatasetLoad): string {
  const number = new Intl.NumberFormat()
  const counts = COUNTS.filter(([key]) => load[key] > 0).map(
    ([key, word]) => `${number.format(load[key])} ${word}`,
  )
  const parts = [...(counts.length ? counts : ['no rows']), secondsText(loadSeconds(load))]
  return `${MODE_WORDS[load.mode]}: ${parts.join(' · ')}`
}
