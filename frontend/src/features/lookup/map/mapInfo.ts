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

const NAMES = new Map(STATES.map((state) => [state.code, state.name]))

/** A state's full name ("WA" → "Washington"), or the code itself for one the map lacks. */
export function stateName(code: string): string {
  return NAMES.get(code) ?? code
}
