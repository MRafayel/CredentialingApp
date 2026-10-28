/** Which results of the latest check the Results section lists: everything but a clean answer. */
import type { Verification } from './api.ts'

/** False only for a completed check that found no record; every other answer stays in view. */
export function needsAttention(result: Pick<Verification, 'outcome' | 'finding'>): boolean {
  return !(result.outcome === 'completed' && result.finding === 'no_record')
}
