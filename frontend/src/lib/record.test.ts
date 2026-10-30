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
  rewritten: false,
  name: 'Samplespeling, Avery',
  license_number: 'QZ001',
  license_state: 'NY',
  npi: '1000000014',
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

  it('shows a row that left the list and came back as leaving, then back', () => {
    const back: RecordVersion = { ...first, valid_from: '2026-10-27T08:00:00Z', valid_to: null }

    expect(recordSteps([first, back], UTC)).toEqual([
      {
        key: 'back-2026-10-27T08:00:00Z',
        kind: 'back',
        at: '2026-10-27T08:00:00Z',
        title: 'Back in this list',
        detail: null,
      },
      {
        key: 'left-2026-10-20T08:00:00Z',
        kind: 'left',
        at: '2026-10-20T08:00:00Z',
        title: 'No longer in this list',
        detail: null,
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

  it('says what changed when a row comes back different, and when it left again', () => {
    const back: RecordVersion = {
      ...first,
      valid_from: '2026-10-27T08:00:00Z',
      valid_to: '2026-10-29T08:00:00Z',
      end_date: '2026-10-15',
    }

    expect(recordSteps([first, back], UTC).map((step) => [step.kind, step.detail])).toEqual([
      ['left', null],
      ['back', 'End date Oct 15, 2026'],
      ['left', null],
      ['seen', 'Termination · as published: Terminate Medicaid Authority'],
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

  it('shows a rewrite by the list as one step saying what is new', () => {
    const rewritten: RecordVersion = {
      ...first,
      valid_from: '2026-10-20T08:00:00Z',
      valid_to: null,
      rewritten: true,
      name: 'Samplespelling, Avery',
      license_number: 'QZ002',
    }

    expect(recordSteps([first, rewritten], UTC)).toEqual([
      {
        key: 'rewritten-2026-10-20T08:00:00Z',
        kind: 'rewritten',
        at: '2026-10-20T08:00:00Z',
        title: 'Row rewritten by the list',
        detail: 'Name now Samplespelling, Avery · Licence now QZ002 NY',
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

  it('names a new NPI and other changes on a rewrite, and the row leaving later', () => {
    const before = { ...first, valid_to: '2026-10-20T08:00:00Z' }
    const after: RecordVersion = {
      ...first,
      valid_from: '2026-10-20T08:00:00Z',
      valid_to: '2026-10-27T08:00:00Z',
      rewritten: true,
      npi: '1000000022',
      end_date: '2026-10-15',
    }

    expect(recordSteps([before, after], UTC).map((step) => [step.kind, step.detail])).toEqual([
      ['left', null],
      ['rewritten', 'NPI now 1000000022 · End date Oct 15, 2026'],
      ['seen', 'Termination · as published: Terminate Medicaid Authority'],
    ])
  })

  it('calls a rewrite that changed nothing shown a change to other details', () => {
    const after = { ...first, valid_from: '2026-10-20T08:00:00Z', valid_to: null, rewritten: true }

    expect(recordSteps([first, after], UTC)[0]?.detail).toBe('Other details of the row changed')
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
