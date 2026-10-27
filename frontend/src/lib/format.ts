/**
 * Times for people: short relative wording ("5 min ago", "in 6 d", "overdue 3 h") that matches
 * the command line, and absolute times in the viewer's locale with a UTC title for hover.
 * Pure functions of a moment and "now", so they are tested without a clock.
 */

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

type Moment = Date | string

export type FormatOptions = { locale?: string; timeZone?: string }

function toDate(moment: Moment): Date {
  return typeof moment === 'string' ? new Date(moment) : moment
}

/** A non-negative duration in its largest whole unit: "45 s", "35 min", "3 h", "7 d". */
export function span(milliseconds: number): string {
  const ms = Math.max(0, milliseconds)
  if (ms >= DAY) return `${Math.floor(ms / DAY)} d`
  if (ms >= HOUR) return `${Math.floor(ms / HOUR)} h`
  if (ms >= MINUTE) return `${Math.floor(ms / MINUTE)} min`
  return `${Math.floor(ms / 1000)} s`
}

/** How long before `now` the moment was; a moment in the future reads "just now". */
export function ago(moment: Moment, now: Date): string {
  const elapsed = now.getTime() - toDate(moment).getTime()
  if (elapsed < MINUTE) return 'just now'
  if (elapsed >= DAY && elapsed < 2 * DAY) return 'yesterday'
  return `${span(elapsed)} ago`
}

/** How long until the moment is due, or how long overdue it already is. */
export function until(moment: Moment, now: Date): string {
  const remaining = toDate(moment).getTime() - now.getTime()
  if (Math.abs(remaining) < MINUTE) return 'due now'
  return remaining > 0 ? `in ${span(remaining)}` : `overdue ${span(-remaining)}`
}

/** "Oct 26, 2026, 3:19 PM" in the viewer's locale. */
export function dateTime(moment: Moment, { locale, timeZone }: FormatOptions = {}): string {
  return new Intl.DateTimeFormat(locale, {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone,
  }).format(toDate(moment))
}

/** "Oct 26, 2026" in the viewer's locale. */
export function date(moment: Moment, { locale, timeZone }: FormatOptions = {}): string {
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeZone }).format(toDate(moment))
}

/** A calendar date from the API ("2024-03-01") without shifting it across time zones. */
export function calendarDate(value: string, { locale }: FormatOptions = {}): string {
  const [year, month, day] = value.split('-').map(Number)
  if (!year || !month || !day) return value
  const moment = new Date(Date.UTC(year, month - 1, day))
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeZone: 'UTC' }).format(moment)
}

/** The exact moment in UTC, for a `title`: "2026-10-26 15:19 UTC". */
export function utcTitle(moment: Moment): string {
  const iso = toDate(moment).toISOString()
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`
}

/** An event time with its day: "Today, 3:19 PM", "Yesterday, 6:12 PM", "Oct 5, 4:40 PM". */
export function eventTime(moment: Moment, now: Date, options: FormatOptions = {}): string {
  const { locale, timeZone } = options
  const at = toDate(moment)
  const dayKey = (value: Date) =>
    new Intl.DateTimeFormat('en-CA', { timeZone, dateStyle: 'short' }).format(value)
  const time = new Intl.DateTimeFormat(locale, { timeStyle: 'short', timeZone }).format(at)
  if (dayKey(at) === dayKey(now)) return `Today, ${time}`
  if (dayKey(at) === dayKey(new Date(now.getTime() - DAY))) return `Yesterday, ${time}`
  const sameYear =
    new Intl.DateTimeFormat('en', { year: 'numeric', timeZone }).format(at) ===
    new Intl.DateTimeFormat('en', { year: 'numeric', timeZone }).format(now)
  const day = new Intl.DateTimeFormat(locale, {
    month: 'short',
    day: 'numeric',
    year: sameYear ? undefined : 'numeric',
    timeZone,
  }).format(at)
  return `${day}, ${time}`
}

/** A count with its noun: "1 list", "2 lists". */
export function plural(count: number, noun: string, nouns = `${noun}s`): string {
  return `${new Intl.NumberFormat().format(count)} ${count === 1 ? noun : nouns}`
}
