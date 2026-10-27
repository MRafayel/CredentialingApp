import { describe, expect, it } from 'vitest'
import {
  ago,
  calendarDate,
  date,
  dateTime,
  eventTime,
  plural,
  span,
  until,
  utcTitle,
} from './format.ts'

const NOW = new Date('2026-10-26T15:19:00Z')
const minutes = (n: number) => new Date(NOW.getTime() - n * 60_000)
const UTC = { locale: 'en-US', timeZone: 'UTC' }
const plain = (text: string) => text.replace(/ /g, ' ')

describe('span', () => {
  it('uses the largest whole unit', () => {
    expect(span(45_000)).toBe('45 s')
    expect(span(35 * 60_000)).toBe('35 min')
    expect(span(3.9 * 3_600_000)).toBe('3 h')
    expect(span(7 * 86_400_000)).toBe('7 d')
    expect(span(-5)).toBe('0 s')
  })
})

describe('ago', () => {
  it('reads like the command line', () => {
    expect(ago(minutes(0.5), NOW)).toBe('just now')
    expect(ago(new Date(NOW.getTime() + 60_000), NOW)).toBe('just now')
    expect(ago(minutes(5), NOW)).toBe('5 min ago')
    expect(ago(minutes(180), NOW)).toBe('3 h ago')
    expect(ago(minutes(60 * 30), NOW)).toBe('yesterday')
    expect(ago(minutes(60 * 24 * 3), NOW)).toBe('3 d ago')
    expect(ago(minutes(5).toISOString(), NOW)).toBe('5 min ago')
  })
})

describe('until', () => {
  it('says when something is due or how overdue it is', () => {
    expect(until(new Date(NOW.getTime() + 6.5 * 86_400_000), NOW)).toBe('in 6 d')
    expect(until(minutes(180), NOW)).toBe('overdue 3 h')
    expect(until(minutes(0.2), NOW)).toBe('due now')
  })
})

describe('absolute times', () => {
  it('formats in the requested locale and zone', () => {
    expect(plain(dateTime(NOW, UTC))).toBe('Oct 26, 2026, 3:19 PM')
    expect(date(NOW, UTC)).toBe('Oct 26, 2026')
    expect(utcTitle(NOW)).toBe('2026-10-26 15:19 UTC')
  })

  it('keeps calendar dates on their day in any zone', () => {
    expect(calendarDate('2024-03-01', { locale: 'en-US' })).toBe('Mar 1, 2024')
    expect(calendarDate('not a date')).toBe('not a date')
  })

  it('names the day of an event, with its time', () => {
    expect(plain(eventTime(minutes(60), NOW, UTC))).toBe('Today, 2:19 PM')
    expect(plain(eventTime(minutes(60 * 24), NOW, UTC))).toBe('Yesterday, 3:19 PM')
    expect(plain(eventTime(new Date('2026-10-05T16:40:00Z'), NOW, UTC))).toBe('Oct 5, 4:40 PM')
    expect(plain(eventTime(new Date('2025-10-05T16:40:00Z'), NOW, UTC))).toBe(
      'Oct 5, 2025, 4:40 PM',
    )
  })
})

describe('plural', () => {
  it('matches the noun to the count', () => {
    expect(plural(1, 'list')).toBe('1 list')
    expect(plural(2, 'list')).toBe('2 lists')
    expect(plural(0, 'source')).toBe('0 sources')
  })
})
