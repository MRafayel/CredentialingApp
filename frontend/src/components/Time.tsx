import { ago, calendarDate, date, dateTime, until, utcTitle } from '../lib/format.ts'
import { useNow } from './useNow.ts'

const title = (at: string) => `${dateTime(at)} · ${utcTitle(at)}`

/** "5 min ago", with the exact time on hover. */
export function Ago({ at, className }: { at: string; className?: string }) {
  const now = useNow()
  return (
    <time dateTime={at} title={title(at)} className={className}>
      {ago(at, now)}
    </time>
  )
}

/** "in 6 d" or "overdue 3 h", with the exact time on hover. */
export function Until({ at, className }: { at: string; className?: string }) {
  const now = useNow()
  return (
    <time dateTime={at} title={title(at)} className={className}>
      {until(at, now)}
    </time>
  )
}

/** "Oct 26, 2026" for a moment, with the exact time on hover. */
export function DateOf({ at, className }: { at: string; className?: string }) {
  return (
    <time dateTime={at} title={title(at)} className={className}>
      {date(at)}
    </time>
  )
}

/** A calendar date from the API ("2024-03-01"), never shifted by time zone. */
export function CalendarDate({ value }: { value: string }) {
  return <time dateTime={value}>{calendarDate(value)}</time>
}
