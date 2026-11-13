import { describe, expect, it } from 'vitest'
import type { ImportProblem, ImportRow } from './api.ts'
import {
  columnsLine,
  editableRows,
  filledLines,
  addedLine,
  importedLine,
  importSummary,
  pageAfterRemoval,
  problemLabel,
  rebuildText,
  rowsWithErrors,
  rowsWithProblems,
  sanitizeCell,
  type EditableRow,
} from './importing.ts'

const problem = (
  overrides: Partial<ImportProblem> & Pick<ImportProblem, 'code'>,
): ImportProblem => ({
  severity: 'error',
  detail: null,
  ...overrides,
})

const row = (line: number, problems: ImportProblem[] = []): ImportRow => ({
  line,
  display: 'Sampleperson, Avery',
  entity_kind: 'individual',
  npi: null,
  role: 'workforce',
  has_date_of_birth: false,
  name_split: false,
  importable: !problems.some((p) => p.severity === 'error'),
  problems,
  duplicate_of_line: null,
  existing_provider_id: null,
  values: { last: 'Sampleperson', first: 'Avery', role: 'workforce' },
})

describe('problemLabel', () => {
  it('reads an error red and a warning amber, with the backend detail', () => {
    expect(problemLabel(problem({ code: 'duplicate_in_file', detail: 'line 3' }))).toEqual({
      tone: 'listed',
      word: 'Duplicate',
      detail: 'line 3',
    })
    expect(
      problemLabel(problem({ code: 'date_of_birth_unreadable', severity: 'warning' })),
    ).toEqual({
      tone: 'review',
      word: 'Date of birth unreadable',
    })
  })

  it('has a word for every code', () => {
    const codes: ImportProblem['code'][] = [
      'name_missing',
      'npi_invalid',
      'duplicate_in_file',
      'already_on_roster',
      'invalid_row',
      'date_of_birth_unreadable',
      'license_state_invalid',
      'license_incomplete',
      'role_unrecognized',
    ]
    for (const code of codes) expect(problemLabel(problem({ code })).word).toBeTruthy()
  })
})

describe('the preview', () => {
  it('waits for a header and one row', () => {
    expect(filledLines('Last\tFirst\n')).toBe(1)
    expect(filledLines('Last,First\r\n\r\nSampleperson,Avery\n')).toBe(2)
  })

  it('sums up the rows in the backend counts, leaving zeros out', () => {
    expect(importSummary({ importable: 42, with_warnings: 3, rejected: 2 })).toBe(
      '42 ready · 3 with warnings · 2 rejected',
    )
    expect(importSummary({ importable: 5, with_warnings: 0, rejected: 0 })).toBe('5 ready')
    expect(importSummary({ importable: 0, with_warnings: 0, rejected: 4 })).toBe(
      '0 ready · 4 rejected',
    )
  })

  it('names the columns read', () => {
    expect(columnsLine(['last', 'first', 'npi'])).toBe('Read Last name, First name, NPI')
  })

  it('keeps the rows with any problem', () => {
    const rows = [row(1), row(2, [problem({ code: 'npi_invalid' })]), row(3)]
    expect(rowsWithProblems(rows).map((r) => r.line)).toEqual([2])
  })
})

describe('editing the preview', () => {
  const edited = (id: number, values: EditableRow['values']): EditableRow => ({ id, values })

  it('rebuilds a header of column names and one tab-separated line per row', () => {
    const rows = [
      edited(1, { last: 'Mockford', first: 'Casey', npi: '9990000017' }),
      edited(2, { last: 'Testwell', first: 'Jordan' }),
    ]
    expect(rebuildText(['last', 'first', 'npi'], rows)).toBe(
      'last\tfirst\tnpi\nMockford\tCasey\t9990000017\nTestwell\tJordan\t',
    )
  })

  it('flattens tabs and line breaks inside a cell and quotes a cell with a quote', () => {
    expect(sanitizeCell('Sample\tville\r\nMedical')).toBe('Sample ville Medical')
    expect(sanitizeCell('Riley "RJ"')).toBe('"Riley ""RJ"""')
    expect(
      rebuildText(['last', 'first'], [edited(1, { last: 'Quill\nby', first: 'Harper' })]),
    ).toBe('last\tfirst\nQuill by\tHarper')
  })

  it('leaves out a row whose every value is blank', () => {
    const rows = [
      edited(1, { last: ' ', first: '' }),
      edited(2, { last: 'Stubbs', first: 'Jamie' }),
      edited(3, {}),
    ]
    expect(rebuildText(['last', 'first'], rows)).toBe('last\tfirst\nStubbs\tJamie')
  })

  it('keeps one column tab-separated so a comma stays in its cell', () => {
    expect(rebuildText(['name'], [edited(1, { name: 'Sampleperson, Avery' })])).toBe(
      'name\t\nSampleperson, Avery',
    )
  })

  it('numbers the rows of the first preview once; a removal re-indexes nothing', () => {
    const rows = editableRows({ rows: [row(1), row(2), row(3)] })
    expect(rows.map((r) => r.id)).toEqual([1, 2, 3])
    expect(rows[0]?.values).toEqual({ last: 'Sampleperson', first: 'Avery', role: 'workforce' })
    expect(rows.filter((r) => r.id !== 2).map((r) => r.id)).toEqual([1, 3])
  })

  it('copies the values, so an edit never reaches the report', () => {
    const report = { rows: [row(1)] }
    const [first] = editableRows(report)
    if (first) first.values.last = 'Changed'
    expect(report.rows[0]?.values.last).toBe('Sampleperson')
  })

  it('picks the rows with an error, never a warning alone', () => {
    const rows = [
      row(1, [problem({ code: 'role_unrecognized', severity: 'warning' })]),
      row(2, [problem({ code: 'duplicate_in_file', detail: 'line 1' })]),
      row(3),
      row(4, [
        problem({ code: 'license_incomplete', severity: 'warning' }),
        problem({ code: 'npi_invalid' }),
      ]),
    ]
    expect(rowsWithErrors(rows).map((r) => r.line)).toEqual([2, 4])
  })

  it('steps back a page when a removal empties the current one', () => {
    expect(pageAfterRemoval(3, 20, 10)).toBe(2)
    expect(pageAfterRemoval(2, 11, 10)).toBe(2)
    expect(pageAfterRemoval(1, 0, 10)).toBe(1)
  })
})

describe('importedLine', () => {
  it('counts how many were added and how many are being checked after the answer', () => {
    expect(importedLine({ imported: Array(42).fill('id'), checking: 42 })).toEqual({
      added: 42,
      checking: 42,
    })
  })

  it('counts nobody being checked when nothing was imported', () => {
    expect(importedLine({ imported: [], checking: 0 })).toEqual({ added: 0, checking: 0 })
  })
})

describe('addedLine', () => {
  it('counts those checked so far against those added while the first check runs', () => {
    expect(addedLine(5000, 1240)).toBe('3,760 of 5,000 providers added')
    expect(addedLine(42, 42)).toBe('0 of 42 providers added')
    expect(addedLine(1, 1)).toBe('0 of 1 provider added')
  })

  it('says how many were added once nobody is waiting, and never over-counts', () => {
    expect(addedLine(42, 0)).toBe('42 providers added')
    expect(addedLine(42, 60)).toBe('0 of 42 providers added')
    expect(addedLine(0, 0)).toBe('0 providers added')
  })
})
