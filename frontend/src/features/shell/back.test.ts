import { describe, expect, it } from 'vitest'
import { backFrom, fromHere, type Back } from './back.ts'

const LOOKUP: Back = { to: '/lookup', label: 'Lookup' }
const PROVIDER: Back = { to: '/providers/p-1', label: 'Sampleperson, Avery' }

describe('backFrom', () => {
  it('leads to the opener when the state names it', () => {
    expect(backFrom({ from: PROVIDER }, LOOKUP)).toEqual(PROVIDER)
  })

  it('keeps the opener’s own state, so its Back survives the way back', () => {
    const state = { from: { to: '/lookup/r-1', label: 'Example, Jo', state: { from: PROVIDER } } }
    expect(backFrom(state, LOOKUP)).toEqual({
      to: '/lookup/r-1',
      label: 'Example, Jo',
      state: { from: PROVIDER },
    })
  })

  it('leads to the fallback otherwise', () => {
    expect(backFrom(null, LOOKUP)).toEqual(LOOKUP)
    expect(backFrom(undefined, LOOKUP)).toEqual(LOOKUP)
    expect(backFrom({ from: 'elsewhere' }, LOOKUP)).toEqual(LOOKUP)
    expect(backFrom({ from: { to: 3, label: 'x' } }, LOOKUP)).toEqual(LOOKUP)
    expect(backFrom({ imported: 'Added 3' }, PROVIDER)).toEqual(PROVIDER)
  })
})

describe('fromHere', () => {
  it('names the page a link leaves', () => {
    expect(fromHere(PROVIDER)).toEqual({ from: PROVIDER })
    expect(backFrom(fromHere(LOOKUP), PROVIDER)).toEqual(LOOKUP)
  })

  it('carries the page’s own way back, and nothing else of its state', () => {
    const record: Back = {
      to: '/lookup/r-1',
      label: 'Example, Jo',
      state: { from: PROVIDER, scroll: 120 },
    }
    expect(fromHere(record)).toEqual({
      from: { to: '/lookup/r-1', label: 'Example, Jo', state: { from: PROVIDER } },
    })
    expect(fromHere({ ...LOOKUP, state: { imported: 'x' } })).toEqual({ from: LOOKUP })
  })

  it('remembers a bounded number of pages', () => {
    let here: Back = PROVIDER
    for (let index = 0; index < 20; index += 1) {
      here = { to: `/lookup/r-${index}`, label: `Record ${index}`, state: fromHere(here) }
    }
    let depth = 0
    let state: unknown = fromHere(here)
    while (state !== undefined) {
      depth += 1
      state = backFrom(state, LOOKUP).state
    }
    expect(depth).toBe(8)
  })
})
