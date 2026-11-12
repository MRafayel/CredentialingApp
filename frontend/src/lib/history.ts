/** The verification history as the timeline shows it: grouped into check runs, nothing more. */
import type { Verification } from './api.ts'
import { changedFromLabel } from './labels.ts'

export type CheckRun = { at: string; results: Verification[] }

/** Newest first; results stored together keep a stable order by source. */
const byNewest = (a: Verification, b: Verification) =>
  Date.parse(b.created_at) - Date.parse(a.created_at) || a.source_id.localeCompare(b.source_id)

/** Results stored together (same `created_at`) are one check run; newest run first. */
export function checkRuns(history: readonly Verification[]): CheckRun[] {
  const runs = new Map<string, Verification[]>()
  for (const result of [...history].sort(byNewest)) {
    const key = new Date(result.created_at).toISOString()
    runs.set(key, [...(runs.get(key) ?? []), result])
  }
  return [...runs].map(([at, results]) => ({ at, results }))
}

/** "changed from Record found": what the result said before, on a result that changed. */
export function changeLine(result: Pick<Verification, 'changed_from'>): string | null {
  return result.changed_from ? `changed from ${changedFromLabel(result.changed_from).word}` : null
}
