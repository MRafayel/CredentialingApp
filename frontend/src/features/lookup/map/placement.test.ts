import { describe, expect, it } from 'vitest'
import { placeLabel } from './placement.ts'

const BASE = { block: { width: 160, height: 40 }, width: 860, line: 72, minTop: -60 }

describe('placeLabel', () => {
  it('centres the text on a full line and sits it 8 px above the line', () => {
    expect(placeLabel({ ...BASE, anchor: { x: 430, y: 300 } })).toEqual({
      left: 350,
      top: 180,
      line: 72,
    })
  })

  it('keeps the full line when the text just reaches minTop', () => {
    expect(placeLabel({ ...BASE, anchor: { x: 430, y: 60 } })).toMatchObject({
      top: -60,
      line: 72,
    })
  })

  it('shortens the line so the text stops at minTop', () => {
    // A full line would put the top at -80; 20 px shorter puts it at -60.
    expect(placeLabel({ ...BASE, anchor: { x: 430, y: 40 } })).toMatchObject({
      top: -60,
      line: 52,
    })
  })

  it('never draws less than 24 px of line; the text then stays at minTop', () => {
    expect(placeLabel({ ...BASE, anchor: { x: 430, y: -10 } })).toMatchObject({
      top: -60,
      line: 24,
    })
  })

  it('keeps the text 16 px inside the stage', () => {
    expect(placeLabel({ ...BASE, anchor: { x: 20, y: 300 } }).left).toBe(16)
    expect(placeLabel({ ...BASE, anchor: { x: 850, y: 300 } }).left).toBe(684)
  })

  it('pins a block wider than the stage to the left margin', () => {
    expect(placeLabel({ ...BASE, width: 150, anchor: { x: 75, y: 300 } }).left).toBe(16)
  })
})
