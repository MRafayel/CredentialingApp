import { describe, expect, it } from 'vitest'
import { hasValidCheckDigit, npiState } from './npi.ts'

describe('hasValidCheckDigit', () => {
  it('accepts the published CMS example and rejects a changed check digit', () => {
    expect(hasValidCheckDigit('1234567893')).toBe(true)
    expect(hasValidCheckDigit('1234567890')).toBe(false)
  })

  it('rejects anything that is not ten digits', () => {
    expect(hasValidCheckDigit('123456789')).toBe(false)
    expect(hasValidCheckDigit('12345678930')).toBe(false)
    expect(hasValidCheckDigit('12345678a3')).toBe(false)
  })
})

describe('npiState', () => {
  it('names each stage of typing', () => {
    expect(npiState('')).toBe('empty')
    expect(npiState('12345')).toBe('incomplete')
    expect(npiState('1234567890')).toBe('mismatch')
    expect(npiState('1234567893')).toBe('valid')
  })
})
