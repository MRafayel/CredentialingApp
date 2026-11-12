import { describe, expect, it } from 'vitest'
import {
  REQUIRED,
  TEN_DIGITS,
  TWO_LETTER_STATE,
  missing,
  npiError,
  stateError,
  without,
} from './forms.ts'

describe('missing', () => {
  it('names every blank field and nothing else', () => {
    expect(missing({ first: ' ', last: 'Sampleperson', organization: '' })).toEqual({
      first: REQUIRED,
      organization: REQUIRED,
    })
  })
})

describe('stateError', () => {
  it('accepts blank and two upper-case letters', () => {
    expect(stateError('')).toBeUndefined()
    expect(stateError('NY')).toBeUndefined()
  })
  it('refuses one letter', () => {
    expect(stateError('N')).toBe(TWO_LETTER_STATE)
  })
})

describe('npiError', () => {
  it('accepts blank and ten digits', () => {
    expect(npiError('')).toBeUndefined()
    expect(npiError(' 1234567893 ')).toBeUndefined()
  })
  it('refuses anything else', () => {
    expect(npiError('12345')).toBe(TEN_DIGITS)
  })
})

describe('without', () => {
  it('drops the key and keeps the rest', () => {
    expect(without({ first: REQUIRED, last: REQUIRED }, 'first')).toEqual({ last: REQUIRED })
  })
  it('returns the same object when the key is absent', () => {
    const errors = { last: REQUIRED }
    expect(without(errors, 'first')).toBe(errors)
  })
})
