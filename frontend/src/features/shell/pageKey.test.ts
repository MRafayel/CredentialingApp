import { describe, expect, it } from 'vitest'
import { pageKey } from './pageKey.ts'

describe('pageKey', () => {
  it('keeps the sheets over the roster on the roster', () => {
    expect(pageKey('/providers/new')).toBe('/providers')
    expect(pageKey('/providers/import')).toBe('/providers')
  })

  it('keeps the dialogs over a provider on the provider page', () => {
    expect(pageKey('/providers/p-1/registry')).toBe('/providers/p-1')
    expect(pageKey('/providers/p-1/record/r-9')).toBe('/providers/p-1')
    expect(pageKey('/providers/p-1/record/r-9/')).toBe('/providers/p-1')
  })

  it('gives every other path its own key', () => {
    expect(pageKey('/providers/p-1')).toBe('/providers/p-1')
    expect(pageKey('/providers/p-1/record')).toBe('/providers/p-1/record')
    expect(pageKey('/providers/p-1/record/r-9/x')).toBe('/providers/p-1/record/r-9/x')
    expect(pageKey('/lookup/r-9')).toBe('/lookup/r-9')
  })
})
