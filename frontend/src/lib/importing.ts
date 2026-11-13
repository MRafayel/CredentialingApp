/**
 * An import in words: each problem the backend found as a mark and a word, the preview's
 * summary line, and the note the roster shows after a commit. The backend reads the file and
 * decides every problem; nothing here judges a row.
 */
import type { ImportColumn, ImportProblem, ImportReport, ImportRow } from './api.ts'
import { plural } from './format.ts'
import type { Label } from './labels.ts'

/**
 * The largest text the backend reads (its own limit, 2 MB); a bigger file is not read into the
 * page at all, so a browser tab never holds a file the import would refuse.
 */
export const IMPORT_MAX_BYTES = 2 * 1024 * 1024

/** A preview waits for a header and one row. */
export const IMPORT_MIN_LINES = 2

const PROBLEM_WORDS: Record<ImportProblem['code'], string> = {
  name_missing: 'No name',
  npi_invalid: 'NPI invalid',
  duplicate_in_file: 'Duplicate',
  already_on_roster: 'Already on roster',
  invalid_row: 'Row not readable',
  date_of_birth_unreadable: 'Date of birth unreadable',
  license_state_invalid: 'Licence state invalid',
  license_incomplete: 'Licence incomplete',
  role_unrecognized: 'Role not recognized',
}

/** A problem as a mark and a word: red when it keeps the row out, amber when it drops a value. */
export function problemLabel(problem: ImportProblem): Label {
  return {
    tone: problem.severity === 'error' ? 'listed' : 'review',
    word: PROBLEM_WORDS[problem.code],
    ...(problem.detail ? { detail: problem.detail } : {}),
  }
}

/** Lines that hold anything: a header and one row make a preview. */
export function filledLines(text: string): number {
  return text.split(/\r\n|\r|\n/).filter((line) => line.trim() !== '').length
}

/** "42 ready · 3 with warnings · 2 rejected"; a zero is left out, except for "ready". */
export function importSummary(
  report: Pick<ImportReport, 'importable' | 'with_warnings' | 'rejected'>,
): string {
  const format = new Intl.NumberFormat().format
  return [
    `${format(report.importable)} ready`,
    report.with_warnings ? `${format(report.with_warnings)} with warnings` : null,
    report.rejected ? `${format(report.rejected)} rejected` : null,
  ]
    .filter(Boolean)
    .join(' · ')
}

const COLUMN_WORDS: Record<ImportColumn, string> = {
  last: 'Last name',
  first: 'First name',
  middle: 'Middle',
  suffix: 'Suffix',
  name: 'Name',
  organization: 'Organization',
  npi: 'NPI',
  date_of_birth: 'Date of birth',
  license_number: 'Licence',
  license_state: 'Licence state',
  role: 'Role',
}

/** A column's name as its field label says it: "Last name", "Licence state". */
export function columnWord(column: ImportColumn): string {
  return COLUMN_WORDS[column]
}

/** The columns the backend read, in file order: "Read Last name, First name, NPI". */
export function columnsLine(columns: readonly ImportColumn[]): string {
  return `Read ${columns.map((column) => COLUMN_WORDS[column]).join(', ')}`
}

/** The rows with a problem of any kind. */
export function rowsWithProblems<Row extends Pick<ImportRow, 'problems'>>(
  rows: readonly Row[],
): Row[] {
  return rows.filter((row) => row.problems.length > 0)
}

/** The rows with a problem that keeps them out of the import (an error, not a warning). */
export function rowsWithErrors<Row extends Pick<ImportRow, 'problems'>>(
  rows: readonly Row[],
): Row[] {
  return rows.filter((row) => row.problems.some((problem) => problem.severity === 'error'))
}

/**
 * A preview row the person can correct or drop: the text of each recognized column as typed.
 * `id` stays with the row through every edit and removal; the position is never its identity.
 */
export type EditableRow = {
  id: number
  values: Partial<Record<ImportColumn, string>>
}

/** The editing surface of a first preview: its rows in file order, numbered from 1. */
export function editableRows(report: Pick<ImportReport, 'rows'>): EditableRow[] {
  return report.rows.map((row, index) => ({ id: index + 1, values: { ...row.values } }))
}

/**
 * One cell of the rebuilt text: a tab, carriage return or newline becomes a space, so the cell
 * stays one cell of one line; a cell holding a double quote is quoted (inner quotes doubled),
 * the way the backend's tab-separated reader expects one.
 */
export function sanitizeCell(value: string): string {
  const flat = value.replace(/[\t\r\n]+/g, ' ')
  return flat.includes('"') ? `"${flat.replaceAll('"', '""')}"` : flat
}

/** The rows with text in at least one recognized column; the others are left out. */
export function filledRows(
  columns: readonly ImportColumn[],
  rows: readonly EditableRow[],
): EditableRow[] {
  return rows.filter((row) => columns.some((column) => (row.values[column] ?? '').trim() !== ''))
}

/**
 * The text an edited preview sends back: a header of column names (each is a header the
 * backend reads as itself) and one tab-separated line per filled row, in column order. A
 * one-column header ends with a tab, so the text still reads as tab-separated and a comma
 * inside a name stays in its cell.
 */
export function rebuildText(
  columns: readonly ImportColumn[],
  rows: readonly EditableRow[],
): string {
  const header = columns.join('\t') + (columns.length === 1 ? '\t' : '')
  const lines = filledRows(columns, rows).map((row) =>
    columns.map((column) => sanitizeCell(row.values[column] ?? '')).join('\t'),
  )
  return [header, ...lines].join('\n')
}

/** The page to show after rows were removed: the same one, or the last that still has rows. */
export function pageAfterRemoval(page: number, remaining: number, pageSize: number): number {
  return Math.min(page, Math.max(1, Math.ceil(remaining / pageSize)))
}

/**
 * What the roster's note needs after a commit: how many were added, and how many are being
 * checked after the answer (the note follows the roster's summary until none is left).
 */
export function importedLine(report: Pick<ImportReport, 'imported' | 'checking'>): {
  added: number
  checking: number
} {
  return { added: report.imported.length, checking: report.checking }
}

/**
 * The note's one line: "3,760 of 5,000 providers added" while the first check runs (those
 * checked so far count as added, owner, 2026-11-13), "5,000 providers added" once nobody is
 * waiting, and never over-counts.
 */
export function addedLine(added: number, waiting: number): string {
  const total = `${plural(added, 'provider')} added`
  const left = Math.min(Math.max(waiting, 0), added)
  return left === 0 ? total : `${(added - left).toLocaleString()} of ${total}`
}
