import { AnimatePresence, motion, useIsPresent, type Variants } from 'motion/react'
import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
} from 'react'
import { Button } from '../../components/Button.tsx'
import { Field } from '../../components/Field.tsx'
import { Icon } from '../../components/Icon.tsx'
import { quick, settle } from '../../components/motion.ts'
import { ErrorNote } from '../../components/Note.tsx'
import { Sheet } from '../../components/Sheet.tsx'
import { Skeleton } from '../../components/Skeleton.tsx'
import { StatusLabel } from '../../components/Status.tsx'
import type { ImportColumn, ImportProblem, ImportReport, ImportRow } from '../../lib/api.ts'
import { plural } from '../../lib/format.ts'
import {
  IMPORT_MAX_BYTES,
  IMPORT_MIN_LINES,
  columnWord,
  columnsLine,
  editableRows,
  filledLines,
  filledRows,
  importSummary,
  pageAfterRemoval,
  problemLabel,
  rebuildText,
  rowsWithErrors,
  rowsWithProblems,
  type EditableRow,
} from '../../lib/importing.ts'
import { roleWord, type Label } from '../../lib/labels.ts'
import { useImportPreview, useImportProviders } from '../../lib/queries.ts'
import { Chip } from './FilterChips.tsx'
import { Pager } from './Pager.tsx'

/** The preview waits this long after the last keystroke before asking for a dry run. */
const PREVIEW_DELAY_MS = 400
/** Preview rows per page. */
const ROWS = 10

const NO_HEADER: Label = { tone: 'review', word: 'No header found' }
const NOTHING = { importable: 0, with_warnings: 0, rejected: 0 }
/** Desktop columns of a preview row: line, name, NPI, role. */
const ROW_GRID = 'sm:grid-cols-[2.5rem_minmax(0,1fr)_7.5rem_6.5rem]'

/**
 * A row rising in, 20 ms after the one above it. Each row animates itself: rows sit inside their
 * own presence boundary (so a removed row can fold away), which a parent's stagger does not reach.
 */
const ROW_RISE: Variants = {
  hidden: { opacity: 0, y: 10 },
  shown: (order: number) => ({ opacity: 1, y: 0, transition: { ...settle, delay: order * 0.02 } }),
}

/** The text a dry run was asked for, with the ids of the rows it holds (null for a paste). */
type Settled = { text: string; ids: readonly number[] | null }
/** The latest dry-run answer, the text it answers and the row ids its rows stand for. */
type Landed = Settled & { report: ImportReport }
/** The editing surface: the columns and ignored headers of the first preview, and the rows. */
type Table = { columns: ImportColumn[]; ignored: string[]; rows: EditableRow[] }
type RowsUpdate = (update: (rows: EditableRow[]) => EditableRow[]) => void

const EMPTY: Settled = { text: '', ids: null }

/**
 * Many providers at once: paste rows from a spreadsheet or drop a CSV (the file is read here and
 * only its text is sent). Once the backend finds a header, the preview's rows become the
 * editing surface: each row can be corrected or removed, the text is rebuilt from the rows and
 * read again in a dry run as it settles, and "Add N providers" sends that text, storing the ready
 * rows and running their first check.
 */
export function ImportSheet({
  onClose,
  onImported,
}: {
  onClose: () => void
  onImported: (report: ImportReport) => void
}) {
  const fieldId = useId()
  const hintId = useId()
  const fieldRef = useRef<HTMLTextAreaElement>(null)
  const refocus = useRef(false)
  const [text, setText] = useState('')
  const [table, setTable] = useState<Table | null>(null)
  const [settled, setSettled] = useState<Settled>(EMPTY)
  const [landed, setLanded] = useState<Landed | null>(null)
  const [fileError, setFileError] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)

  const rebuilt = useMemo(() => (table ? rebuildText(table.columns, table.rows) : null), [table])
  const filledIds = useMemo(
    () => (table ? filledRows(table.columns, table.rows).map((row) => row.id) : null),
    [table],
  )
  const source = rebuilt ?? text

  useEffect(() => {
    if (source === settled.text) return
    const timer = setTimeout(() => setSettled({ text: source, ids: filledIds }), PREVIEW_DELAY_MS)
    return () => clearTimeout(timer)
  }, [source, filledIds, settled.text])

  useEffect(() => {
    if (table || !refocus.current) return
    refocus.current = false
    fieldRef.current?.focus()
  }, [table])

  const ready = filledLines(settled.text) >= IMPORT_MIN_LINES
  const preview = useImportPreview(settled.text, ready)
  const commit = useImportProviders()

  // The answer for the text it was asked about, kept with the row ids of that text: the
  // placeholder shown while the next dry run is under way belongs to an older text.
  const fresh = ready && !preview.isPlaceholderData ? preview.data : undefined
  if (fresh && fresh !== landed?.report) {
    setLanded({ report: fresh, text: settled.text, ids: settled.ids })
  } else if (!table && landed && !landed.report.no_header && landed.text === text) {
    // A preview with a header turns the paste into rows to correct; the rebuilt text is read
    // at once, so "Add" waits for its own answer.
    const rows = editableRows(landed.report)
    const columns = landed.report.columns
    setTable({ columns, ignored: landed.report.ignored_columns, rows })
    setLanded({ ...landed, ids: rows.map((row) => row.id) })
    setSettled({
      text: rebuildText(columns, rows),
      ids: filledRows(columns, rows).map((row) => row.id),
    })
  }

  const report = ready ? landed?.report : undefined
  // A preview of other text than the one shown: typing, or the dry run still under way.
  const outdated = source !== settled.text || preview.isFetching || landed?.text !== settled.text

  async function read(file: File | undefined) {
    if (!file) return
    if (file.size > IMPORT_MAX_BYTES) {
      setFileError(`${file.name} is over 2 MB. Split it and import each part.`)
      return
    }
    setFileError(null)
    const content = await file.text()
    setText(content)
    setSettled({ text: content, ids: null })
  }

  function onDrop(event: DragEvent<HTMLTextAreaElement>) {
    const file = event.dataTransfer.files[0]
    setDragging(false)
    if (!file) return
    event.preventDefault()
    void read(file)
  }

  function onFile(event: ChangeEvent<HTMLInputElement>) {
    void read(event.target.files?.[0])
    event.target.value = ''
  }

  function startOver() {
    refocus.current = true
    commit.reset()
    setTable(null)
    setText('')
    setSettled(EMPTY)
    setLanded(null)
  }

  const updateRows: RowsUpdate = (update) =>
    setTable((current) => current && { ...current, rows: update(current.rows) })

  const count = report?.importable ?? 0
  const canAdd = report !== undefined && count > 0 && !outdated && !commit.isPending

  return (
    <Sheet
      title="Import providers"
      wide
      onClose={onClose}
      footer={
        <Button
          variant="primary"
          icon="plus"
          className="h-12 w-full text-body"
          disabled={!canAdd}
          aria-busy={commit.isPending}
          onClick={() => commit.mutate(settled.text, { onSuccess: onImported })}
        >
          {commit.isPending
            ? 'Adding…'
            : count > 0
              ? `Add ${plural(count, 'provider')}`
              : 'Add providers'}
        </Button>
      }
    >
      <div className="flex flex-col gap-6 pt-2">
        <AnimatePresence initial={false}>
          {commit.isError && (
            <motion.div
              initial={{ opacity: 0, y: -6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={settle}
            >
              <ErrorNote>{commit.error.message}</ErrorNote>
            </motion.div>
          )}
        </AnimatePresence>

        <AnimatePresence mode="wait" initial={false}>
          {table ? (
            <motion.div
              key="table"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, transition: { duration: 0.12 } }}
              transition={settle}
            >
              <ImportTable
                table={table}
                report={report}
                landed={landed}
                filledIds={filledIds ?? []}
                error={preview.isError ? preview.error.message : null}
                outdated={outdated}
                onRows={updateRows}
                onStartOver={startOver}
              />
            </motion.div>
          ) : (
            <motion.div
              key="paste"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, transition: { duration: 0.12 } }}
              transition={settle}
              className="flex flex-col gap-6"
            >
              <div className="flex flex-col gap-2">
                <label htmlFor={fieldId} className="text-small font-medium text-ink-muted">
                  Paste from a spreadsheet, or drop a CSV
                </label>
                <textarea
                  ref={fieldRef}
                  id={fieldId}
                  value={text}
                  onChange={(event) => setText(event.target.value)}
                  onDragOver={(event) => {
                    if (event.dataTransfer.types.includes('Files')) {
                      event.preventDefault()
                      setDragging(true)
                    }
                  }}
                  onDragLeave={() => setDragging(false)}
                  onDrop={onDrop}
                  data-autofocus=""
                  aria-describedby={hintId}
                  rows={7}
                  spellCheck={false}
                  autoComplete="off"
                  placeholder={'Last name\tFirst name\tNPI\tRole'}
                  className={`min-h-36 w-full resize-y rounded-control border bg-surface px-3 py-2.5 font-mono text-small text-ink transition-colors placeholder:text-ink-muted hover:border-ink-faint ${dragging ? 'border-ink' : 'border-line-strong'}`}
                />
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <label className="inline-flex h-9 cursor-pointer items-center gap-2 rounded-full px-3 text-sm font-medium text-ink-muted transition-colors -ml-3 hover:bg-soft hover:text-ink has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-accent">
                    <Icon name="plus" size={17} />
                    Choose a file
                    <input
                      type="file"
                      accept=".csv,.tsv,.txt,text/csv,text/tab-separated-values,text/plain"
                      onChange={onFile}
                      className="sr-only"
                    />
                  </label>
                  <p id={hintId} className="text-small text-ink-muted">
                    A header row, then one provider per row
                  </p>
                </div>
                {fileError && <ErrorNote>{fileError}</ErrorNote>}
              </div>

              {ready && (
                <PasteStatus
                  report={report}
                  error={preview.isError ? preview.error.message : null}
                  outdated={outdated}
                />
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </Sheet>
  )
}

/** Before the table: the dry run under way, its error, or the text read without a header. */
function PasteStatus({
  report,
  error,
  outdated,
}: {
  report: ImportReport | undefined
  error: string | null
  outdated: boolean
}) {
  if (error && !report) return <ErrorNote>{error}</ErrorNote>
  if (!report?.no_header) return <PreviewSkeleton />
  return (
    <div
      aria-busy={outdated}
      className={`flex flex-col gap-1 transition-opacity duration-200 ${outdated ? 'opacity-60' : ''}`}
    >
      <StatusLabel label={NO_HEADER} chip className="text-body" />
      <p className="text-body text-ink-muted">
        Start with a row naming the columns, such as Last name, First name, NPI.
      </p>
    </div>
  )
}

/** A row as the table shows it: its text, its place, and what the latest dry run read in it. */
type ViewRow = {
  row: EditableRow
  position: number
  /** The backend's reading of the row; undefined while it is blank (left out of the text). */
  read: ImportRow | undefined
  problems: ImportProblem[]
}

/**
 * The preview as the editing surface: the backend's summary, then every row with its problems,
 * each one correctable in place or removable. Ten rows per page, "Problems only" to narrow them,
 * "Remove N with errors" to drop every row the import would refuse.
 */
function ImportTable({
  table,
  report,
  landed,
  filledIds,
  error,
  outdated,
  onRows,
  onStartOver,
}: {
  table: Table
  report: ImportReport | undefined
  landed: Landed | null
  filledIds: readonly number[]
  error: string | null
  outdated: boolean
  onRows: RowsUpdate
  onStartOver: () => void
}) {
  const [problemsOnly, setProblemsOnly] = useState(false)
  const [page, setPage] = useState(1)
  const [open, setOpen] = useState<ReadonlySet<number>>(() => new Set())
  const summaryRef = useRef<HTMLParagraphElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    summaryRef.current?.focus({ preventScroll: true })
  }, [])

  const filled = new Set(filledIds)
  const readings = new Map<number, ImportRow>()
  landed?.ids?.forEach((id, index) => {
    const read = landed.report.rows[index]
    if (read) readings.set(id, read)
  })
  const rows: ViewRow[] = table.rows.map((row, index) => {
    const read = filled.has(row.id) ? readings.get(row.id) : undefined
    return { row, position: index + 1, read, problems: read?.problems ?? [] }
  })
  const withProblems = rowsWithProblems(rows)
  const withErrors = rowsWithErrors(rows)
  const list = problemsOnly ? withProblems : rows
  const pages = Math.max(1, Math.ceil(list.length / ROWS))
  const current = Math.min(page, pages)
  const shown = list.slice((current - 1) * ROWS, current * ROWS)

  function remove(ids: ReadonlySet<number>) {
    const remaining = list.filter((view) => !ids.has(view.row.id))
    const index = list.findIndex((view) => ids.has(view.row.id))
    const next = remaining[Math.min(index, remaining.length - 1)]
    onRows((all) => all.filter((row) => !ids.has(row.id)))
    setOpen((before) => new Set([...before].filter((id) => !ids.has(id))))
    setPage(pageAfterRemoval(current, remaining.length, ROWS))
    // Focus goes to the row that took the removed one's place, else back to the summary.
    requestAnimationFrame(() => {
      const button = next
        ? listRef.current?.querySelector<HTMLElement>(`[data-row-remove="${next.row.id}"]`)
        : null
      if (button) button.focus()
      else summaryRef.current?.focus({ preventScroll: true })
    })
  }

  function edit(id: number, column: ImportColumn, value: string) {
    onRows((all) =>
      all.map((row) =>
        row.id === id ? { ...row, values: { ...row.values, [column]: value } } : row,
      ),
    )
  }

  function toggle(id: number) {
    setOpen((before) => {
      const after = new Set(before)
      if (!after.delete(id)) after.add(id)
      return after
    })
  }

  return (
    <section aria-label="Preview" aria-busy={outdated} className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-3">
        <div
          className={`flex min-w-0 flex-col gap-1 transition-opacity duration-200 ${outdated ? 'opacity-60' : ''}`}
        >
          <p
            ref={summaryRef}
            tabIndex={-1}
            aria-live="polite"
            className="text-title focus:outline-none"
          >
            {importSummary(report ?? NOTHING)}
          </p>
          {table.columns.length > 0 && (
            <p className="text-small text-ink-muted">{columnsLine(table.columns)}</p>
          )}
          {table.ignored.length > 0 && (
            <p className="text-small break-words text-ink-muted">
              Ignored {table.ignored.join(', ')}
            </p>
          )}
        </div>
        <Button variant="ghost" onClick={onStartOver} className="-mt-2 -mr-3 px-3">
          Start over
        </Button>
      </div>

      {(withProblems.length > 0 || problemsOnly || withErrors.length > 0) && (
        <div className="flex flex-wrap items-center gap-2">
          {(withProblems.length > 0 || problemsOnly) && (
            <Chip
              selected={problemsOnly}
              count={withProblems.length}
              onClick={() => {
                setProblemsOnly(!problemsOnly)
                setPage(1)
              }}
            >
              Problems only
            </Chip>
          )}
          {withErrors.length > 0 && (
            <Button
              variant="ghost"
              icon="trash"
              disabled={outdated}
              onClick={() => remove(new Set(withErrors.map((view) => view.row.id)))}
              className="px-3"
            >
              Remove {withErrors.length} with errors
            </Button>
          )}
        </div>
      )}

      {error && <ErrorNote>{error}</ErrorNote>}

      <div ref={listRef}>
        <div
          aria-hidden="true"
          className="hidden items-baseline gap-2 border-b border-line pb-2 text-label sm:flex"
        >
          <div className={`grid min-w-0 flex-1 gap-x-4 ${ROW_GRID}`}>
            <span>Line</span>
            <span>Name</span>
            <span>NPI</span>
            <span>Role</span>
          </div>
          <span className="w-32 shrink-0" />
        </div>
        <AnimatePresence mode="wait" initial={false}>
          <motion.ul
            key={`${problemsOnly}|${current}`}
            exit={{ opacity: 0, transition: { duration: 0.12 } }}
            className="divide-y divide-line"
          >
            <AnimatePresence>
              {shown.map((view, index) => (
                <EditableRowItem
                  key={view.row.id}
                  order={index}
                  view={view}
                  columns={table.columns}
                  outdated={outdated}
                  expanded={open.has(view.row.id)}
                  onToggle={() => toggle(view.row.id)}
                  onRemove={() => remove(new Set([view.row.id]))}
                  onEdit={(column, value) => edit(view.row.id, column, value)}
                />
              ))}
            </AnimatePresence>
          </motion.ul>
        </AnimatePresence>
        {list.length === 0 && (
          <p className="py-6 text-body text-ink-muted">
            {problemsOnly ? 'No problems left' : 'No rows left'}
          </p>
        )}
        {list.length > ROWS && (
          <Pager page={current} pageSize={ROWS} total={list.length} onPage={setPage} />
        )}
      </div>
    </section>
  )
}

/**
 * One row: the backend's reading of it (name, NPI, role, each problem as a mark and a word),
 * "Edit" to open an input per column, and "Remove". Leaving, it folds away.
 */
function EditableRowItem({
  order,
  view,
  columns,
  outdated,
  expanded,
  onToggle,
  onRemove,
  onEdit,
}: {
  /** Its place on the page, for the rise one after another. */
  order: number
  view: ViewRow
  columns: readonly ImportColumn[]
  outdated: boolean
  expanded: boolean
  onToggle: () => void
  onRemove: () => void
  onEdit: (column: ImportColumn, value: string) => void
}) {
  const present = useIsPresent()
  const panelId = useId()
  const { row, read, position, problems } = view
  const role = read?.role ? roleWord(read.role) : null
  const flagged = problems.length > 0
  const dateUnreadable = problems.some((problem) => problem.code === 'date_of_birth_unreadable')

  return (
    <motion.li
      variants={ROW_RISE}
      custom={order}
      initial="hidden"
      animate="shown"
      exit={{ opacity: 0, height: 0, transition: settle }}
      className={present ? undefined : 'overflow-hidden'}
    >
      <div className="flex items-start gap-2 py-3">
        <div
          className={`grid min-w-0 flex-1 gap-x-4 gap-y-1 transition-opacity duration-200 sm:items-baseline ${ROW_GRID} ${outdated ? 'opacity-60' : ''}`}
        >
          <span className="hidden text-small text-ink-muted tabular-nums sm:block">
            <span className="sr-only">Line </span>
            {position}
          </span>
          <div className="min-w-0">
            <p
              className={`truncate text-body font-medium ${read?.importable ? 'text-ink' : 'text-ink-muted'}`}
            >
              {read?.display || '—'}
            </p>
            <p className="text-small text-ink-muted sm:hidden">
              {[`Line ${position}`, read?.npi ? `NPI ${read.npi}` : null, role]
                .filter(Boolean)
                .join(' · ')}
            </p>
            {!read && <p className="text-small text-ink-muted">Empty · left out</p>}
            {(problems.length > 0 || read?.name_split) && (
              <ul className="mt-1 flex flex-col gap-0.5 text-small">
                {problems.map((problem) => {
                  const label = problemLabel(problem)
                  return (
                    <li
                      key={`${problem.code}-${problem.detail ?? ''}`}
                      className="flex flex-wrap gap-x-1.5"
                    >
                      <StatusLabel label={label} size={14} />
                      {label.detail && <span className="text-ink-muted">· {label.detail}</span>}
                    </li>
                  )
                })}
                {read?.name_split && <li className="text-ink-muted">Name split from one column</li>}
              </ul>
            )}
          </div>
          <span className="hidden font-mono text-small text-ink tabular-nums sm:block">
            <span className="sr-only">NPI </span>
            {read?.npi ?? <span className="font-sans text-ink-muted">—</span>}
          </span>
          <span className="hidden text-small text-ink sm:block">
            <span className="sr-only">Role </span>
            {role ?? <span className="text-ink-muted">—</span>}
          </span>
        </div>
        <div className="-my-1 flex shrink-0 items-center justify-end sm:w-32">
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={expanded}
            aria-controls={expanded ? panelId : undefined}
            aria-label={`Edit row ${position}`}
            className={`inline-flex h-11 items-center justify-center gap-1 rounded-full text-sm font-medium transition-colors hover:bg-soft ${flagged ? 'px-3 text-ink' : 'w-11 text-ink-muted hover:text-ink'}`}
          >
            {flagged && <span>Edit</span>}
            <motion.span
              aria-hidden="true"
              animate={{ rotate: expanded ? 180 : 0 }}
              transition={quick}
              className="grid place-items-center"
            >
              <Icon name="chevronDown" />
            </motion.span>
          </button>
          <button
            type="button"
            onClick={onRemove}
            aria-label={`Remove row ${position}`}
            data-row-remove={row.id}
            className="grid size-11 shrink-0 place-items-center rounded-full text-ink-muted transition-colors hover:bg-soft hover:text-status-listed"
          >
            <Icon name="trash" />
          </button>
        </div>
      </div>
      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            key="fields"
            id={panelId}
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={settle}
            className="-mx-1 overflow-hidden"
          >
            <fieldset className="grid grid-cols-2 gap-3 px-1 pt-1 pb-4 sm:grid-cols-3">
              <legend className="sr-only">Row {position}</legend>
              {columns.map((column) => (
                <Field
                  key={column}
                  label={columnWord(column)}
                  value={row.values[column] ?? ''}
                  onChange={(event) => onEdit(column, event.target.value)}
                  hint={
                    column === 'date_of_birth' && dateUnreadable
                      ? 'YYYY-MM-DD or MM/DD/YYYY'
                      : undefined
                  }
                  inputMode={column === 'npi' ? 'numeric' : undefined}
                  inputClassName={column === 'npi' ? 'font-mono tabular-nums' : undefined}
                  spellCheck={false}
                  autoComplete="off"
                />
              ))}
            </fieldset>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.li>
  )
}

function PreviewSkeleton() {
  return (
    <div aria-label="Reading the list" className="flex flex-col gap-3">
      <Skeleton className="h-5 w-64 max-w-full" />
      <Skeleton className="h-3 w-48" />
      {[0, 1, 2, 3].map((index) => (
        <Skeleton key={index} className="h-10 w-full" />
      ))}
    </div>
  )
}
