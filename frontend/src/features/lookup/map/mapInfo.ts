/** Which connected lists cover which state, from `GET /sources` as the backend reports it. */
import type { SourceStatus } from '../../../lib/api.ts'
import { STATES } from './states.ts'

type Covering = Pick<SourceStatus, 'jurisdictions'>
type Counted = Pick<SourceStatus, 'jurisdictions' | 'current_records' | 'data_as_of'>

/** The federal jurisdiction: a list that covers every state. */
const FEDERAL = 'US'

/** The lists whose jurisdictions name this state. */
export function stateLists<T extends Covering>(sources: readonly T[], code: string): T[] {
  return sources.filter((source) => source.jurisdictions.includes(code))
}

/** State codes with at least one list of their own (the federal list is not counted). */
export function coveredStates(sources: readonly Covering[]): Set<string> {
  const codes = new Set<string>()
  for (const source of sources) {
    for (const code of source.jurisdictions) if (code !== FEDERAL) codes.add(code)
  }
  return codes
}

/**
 * Everyone currently on the state's own lists, as the backend counts them; null when the state
 * has no list, so a state we cannot check never reads as one with nobody excluded.
 */
export function totalExcluded(sources: readonly Counted[], code: string): number | null {
  const lists = stateLists(sources, code)
  if (lists.length === 0) return null
  return lists.reduce((sum, source) => sum + source.current_records, 0)
}

/** Whether any of the state's lists has been fetched; until then its count means nothing. */
export function hasFetched(sources: readonly Counted[], code: string): boolean {
  return stateLists(sources, code).some((source) => source.data_as_of !== null)
}

/** The smallest and largest totals among the covered states with data. */
export type HeatScale = { min: number; max: number }

/** The range the heat scale spans: covered states with data only; null when none has any. */
export function heatScale(sources: readonly Counted[]): HeatScale | null {
  let scale: HeatScale | null = null
  for (const code of coveredStates(sources)) {
    const total = totalExcluded(sources, code)
    if (total === null || !hasFetched(sources, code)) continue
    scale = scale
      ? { min: Math.min(scale.min, total), max: Math.max(scale.max, total) }
      : { min: total, max: total }
  }
  return scale
}

/**
 * Where the state sits on the map's heat scale, 0 (fewest excluded) to 1 (most), or null when
 * it has no list or none of its lists has data. Logarithmic, since the totals run from tens to
 * tens of thousands: on a linear scale every state but the largest would read as the lowest.
 */
export function heat(
  sources: readonly Counted[],
  code: string,
  scale: HeatScale | null = heatScale(sources),
): number | null {
  const total = totalExcluded(sources, code)
  if (total === null || scale === null || !hasFetched(sources, code)) return null
  const low = Math.log1p(scale.min)
  const span = Math.log1p(scale.max) - low
  if (span <= 0) return 1
  return Math.min(1, Math.max(0, (Math.log1p(total) - low) / span))
}

const NAMES = new Map(STATES.map((state) => [state.code, state.name]))

/** A state's full name ("WA" → "Washington"), or the code itself for one the map lacks. */
export function stateName(code: string): string {
  return NAMES.get(code) ?? code
}
