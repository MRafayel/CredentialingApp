/**
 * The provider's history as "What happened" lists it: exclusion results stored together are one
 * check run, and each NPI verification is a step of its own, all newest first. The words name
 * what the backend recorded; nothing here decides a status.
 */
import type { Verification } from '../../lib/api.ts'
import { plural } from '../../lib/format.ts'
import { checkRuns } from '../../lib/history.ts'

export type TimelineEntry =
  | { kind: 'run'; at: string; results: Verification[] }
  | { kind: 'npi'; at: string; result: Verification }

const isNpi = (result: Verification) => result.credential_type === 'npi'

/** Check runs and NPI steps in one list, newest first; at the same moment a run comes first. */
export function timelineEntries(history: readonly Verification[]): TimelineEntry[] {
  const runs: TimelineEntry[] = checkRuns(history.filter((result) => !isNpi(result))).map(
    (run) => ({ kind: 'run', ...run }),
  )
  const steps: TimelineEntry[] = history.filter(isNpi).map((result) => ({
    kind: 'npi',
    at: new Date(result.created_at).toISOString(),
    result,
  }))
  const order = (entry: TimelineEntry) => (entry.kind === 'run' ? 0 : 1)
  return [...runs, ...steps].sort(
    (a, b) => Date.parse(b.at) - Date.parse(a.at) || order(a) - order(b),
  )
}

/** The step's title, from the registry status the backend recorded (or why it could not run). */
export function npiStepTitle(result: Pick<Verification, 'details'>): string {
  switch (result.details.npi_status) {
    case 'active':
      return 'NPI verified: Active'
    case 'deactivated':
      return 'NPI deactivated in NPPES'
    case 'not_found':
      return 'NPI not in NPPES'
    case 'mirror_only_active':
      return 'NPI needs review in NPPES'
  }
  return result.details.reason === 'no_npi' ? 'No NPI on file' : 'NPI check could not run'
}

/** Days in an average month, for saying the backend's retention in months. */
const MONTH_DAYS = 30.5

/**
 * The line under the oldest page: how far back the backend keeps history, from its
 * `retention_days`. Whole months when it is at least about one ("6 months" for 183 days), else
 * the days.
 */
export function retentionLine(days: number): string {
  const months = Math.round(days / MONTH_DAYS)
  return `History is kept for ${months >= 1 ? plural(months, 'month') : plural(days, 'day')}`
}
