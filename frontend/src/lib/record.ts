/**
 * How one exclusion record's profile reads: its history in its list as steps, and words for how
 * the backend tied other records to it. Nothing here links records or decides a status.
 */
import type { LinkedRecord, ProfileRecord, RecordProfile, RecordVersion } from './api.ts'
import { calendarDate, type FormatOptions } from './format.ts'
import { actionLabel } from './lookup.ts'

export type RecordStepKind = 'seen' | 'changed' | 'left'

export type RecordStep = {
  key: string
  kind: RecordStepKind
  at: string
  title: string
  detail: string | null
}

const OTHER_DETAILS = 'Other details of the row changed'

const STEP_TITLES: Record<RecordStepKind, string> = {
  seen: 'First seen in this list',
  changed: 'Changed in this list',
  left: 'No longer in this list',
}

/**
 * The record's history in its list, newest first: when it was first seen, each later version
 * with what changed, and when it left the list. `versions` come oldest first, as the API gives
 * them.
 */
export function recordSteps(
  versions: readonly RecordVersion[],
  options: FormatOptions = {},
): RecordStep[] {
  const steps: RecordStep[] = versions.map((version, index) => {
    const previous = versions[index - 1]
    const kind: RecordStepKind = previous ? 'changed' : 'seen'
    const detail = previous ? changes(previous, version, options) : firstDetail(version)
    return {
      key: `${kind}-${version.valid_from}`,
      kind,
      at: version.valid_from,
      // A new version that changed nothing shown here changed something else on the row.
      title: detail === OTHER_DETAILS ? 'Updated in this list' : STEP_TITLES[kind],
      detail,
    }
  })
  const last = versions.at(-1)
  if (last?.valid_to) {
    steps.push({
      key: `left-${last.valid_to}`,
      kind: 'left',
      at: last.valid_to,
      title: STEP_TITLES.left,
      detail: null,
    })
  }
  return steps.reverse()
}

function firstDetail(version: RecordVersion): string {
  const word = actionLabel(version.action).word
  return version.action_raw ? `${word} · as published: ${version.action_raw}` : word
}

/** What a version changed from the one before it, in a few words. */
function changes(before: RecordVersion, after: RecordVersion, options: FormatOptions): string {
  const parts: string[] = []
  if (before.action !== after.action) {
    parts.push(`Action now ${actionLabel(after.action).word.toLowerCase()}`)
  }
  if (before.action_raw !== after.action_raw && after.action_raw) {
    parts.push(`As published: ${after.action_raw}`)
  }
  if (before.end_date !== after.end_date) {
    parts.push(
      after.end_date ? `End date ${calendarDate(after.end_date, options)}` : 'End date removed',
    )
  }
  if (before.reason !== after.reason) {
    parts.push(after.reason ? `Reason: ${after.reason}` : 'Reason removed')
  }
  return parts.length ? parts.join(' · ') : OTHER_DETAILS
}

/** Why the backend tied a record to this one: "Same NPI 1234567893", "Same name only". */
export function linkCaption(record: Pick<LinkedRecord, 'link' | 'via_npi'>): string {
  const npi = record.via_npi ? `NPI ${record.via_npi}` : 'an NPI'
  switch (record.link) {
    case 'by_npi':
      return `Same ${npi}`
    case 'by_license':
      return 'Same licence number and state'
    case 'same_name':
      return 'Same name only'
    case 'listed_as':
      return `Listed under ${npi} from this row`
    case 'named_alongside':
      return `Names ${npi} on its own row`
  }
}

/** Whether the lists hold nothing beyond the record itself (most records). */
export function standsAlone(
  profile: Pick<RecordProfile, 'same_party' | 'related' | 'same_name'> & {
    record: Pick<ProfileRecord, 'related_npis' | 'related_parties'>
  },
): boolean {
  return (
    profile.same_party.length === 0 &&
    profile.related.length === 0 &&
    profile.same_name.length === 0 &&
    profile.record.related_npis.length === 0 &&
    profile.record.related_parties.length === 0
  )
}
