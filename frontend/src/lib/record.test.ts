import { describe, expect, it } from 'vitest'
import type { RecordVersion } from './api.ts'
import { linkCaption, recordSteps, standsAlone } from './record.ts'

const UTC = { locale: 'en-US', timeZone: 'UTC' }

const first: RecordVersion = {
  valid_from: '2026-10-01T08:00:00Z',
  valid_to: '2026-10-20T08:00:00Z',
  action: 'termination',
  action_raw: 'Terminate Medicaid Authority',
  end_date: null,
  reason: null,
}
const ended: RecordVersion = {
  ...first,
  valid_from: '2026-10-20T08:00:00Z',
  valid_to: null,
  end_date: '2026-10-15',
  reason: 'Reinstated',
}

describe('recordSteps', () => {
  it('lists first seen and each change, newest first', () => {
    expect(recordSteps([first, ended], UTC)).toEqual([
      {
        key: 'changed-2026-10-20T08:00:00Z',
        kind: 'changed',
        at: '2026-10-20T08:00:00Z',
        title: 'Changed in this list',
        detail: 'End date Oct 15, 2026 · Reason: Reinstated',
      },
      {
        key: 'seen-2026-10-01T08:00:00Z',
        kind: 'seen',
        at: '2026-10-01T08:00:00Z',
        title: 'First seen in this list',
        detail: 'Termination · as published: Terminate Medicaid Authority',
      },
    ])
  })

  it('ends with leaving the list when the last version was closed', () => {
    const steps = recordSteps([first], UTC)

    expect(steps.map((step) => [step.kind, step.at])).toEqual([
      ['left', '2026-10-20T08:00:00Z'],
      ['seen', '2026-10-01T08:00:00Z'],
    ])
  })

  it('calls a version that changed nothing shown an update to other details', () => {
    const same = { ...first, valid_from: '2026-10-20T08:00:00Z', valid_to: null }
    const step = recordSteps([first, same], UTC)[0]

    expect(step?.title).toBe('Updated in this list')
    expect(step?.detail).toBe('Other details of the row changed')
  })

  it('names a changed action and a removed end date', () => {
    const before = { ...ended, valid_to: '2026-10-25T08:00:00Z' }
    const after = {
      ...ended,
      valid_from: '2026-10-25T08:00:00Z',
      action: 'exclusion' as const,
      end_date: null,
    }

    expect(recordSteps([before, after], UTC)[0]?.detail).toBe(
      'Action now exclusion · End date removed',
    )
  })

  it('is empty without versions', () => {
    expect(recordSteps([])).toEqual([])
  })
})

describe('linkCaption', () => {
  it('says why each record is tied to this one', () => {
    expect(linkCaption({ link: 'by_npi', via_npi: '1234567893' })).toBe('Same NPI 1234567893')
    expect(linkCaption({ link: 'by_license', via_npi: null })).toBe('Same licence number and state')
    expect(linkCaption({ link: 'same_name', via_npi: null })).toBe('Same name only')
    expect(linkCaption({ link: 'listed_as', via_npi: '1234567893' })).toBe(
      'Listed under NPI 1234567893 from this row',
    )
    expect(linkCaption({ link: 'named_alongside', via_npi: '1234567893' })).toBe(
      'Names NPI 1234567893 on its own row',
    )
    expect(linkCaption({ link: 'listed_as', via_npi: null })).toBe(
      'Listed under an NPI from this row',
    )
  })
})

describe('standsAlone', () => {
  const alone = {
    same_party: [],
    related: [],
    same_name: [],
    record: { related_npis: [], related_parties: [] },
  }

  it('is true only when nothing else in the lists points at the record', () => {
    expect(standsAlone(alone)).toBe(true)
    expect(standsAlone({ ...alone, record: { related_npis: [], related_parties: ['X'] } })).toBe(
      false,
    )
  })
})
