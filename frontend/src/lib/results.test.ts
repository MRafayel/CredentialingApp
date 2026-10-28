import { describe, expect, it } from 'vitest'
import { needsAttention } from './results.ts'

describe('needsAttention', () => {
  it('hides a completed check that found no record', () => {
    expect(needsAttention({ outcome: 'completed', finding: 'no_record' })).toBe(false)
  })

  it.each(['record_found', 'related_party', 'ambiguous'] as const)(
    'shows a completed check with finding %s',
    (finding) => {
      expect(needsAttention({ outcome: 'completed', finding })).toBe(true)
    },
  )

  it.each(['source_unavailable', 'blocked', 'manual_required', 'failed'] as const)(
    'shows a %s outcome with no finding',
    (outcome) => {
      expect(needsAttention({ outcome, finding: null })).toBe(true)
    },
  )
})
