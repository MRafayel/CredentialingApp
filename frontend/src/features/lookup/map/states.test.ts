import { describe, expect, it } from 'vitest'
import { labelFor } from './labels.ts'
import { MAP_HEIGHT, MAP_WIDTH, NATION, STATES } from './states.ts'

const inside = ([x, y]: readonly [number, number]) =>
  x >= 0 && x <= MAP_WIDTH && y >= 0 && y <= MAP_HEIGHT

describe('the states map data', () => {
  it('has the 50 states and DC, once each', () => {
    expect(STATES).toHaveLength(51)
    expect(new Set(STATES.map((state) => state.code)).size).toBe(51)
    expect(STATES.map((state) => state.code)).toContain('DC')
  })

  it('draws every state and the nation', () => {
    for (const state of STATES) expect(state.d).toMatch(/^M.+[Zz]$/)
    expect(NATION).toMatch(/^M.+[Zz]$/)
  })

  it('keeps every anchor and label on the canvas', () => {
    for (const state of STATES) {
      expect(inside(state.anchor), state.code).toBe(true)
      expect(inside(labelFor(state).at), state.code).toBe(true)
    }
  })

  it('gives DC, too small to point at, a target that covers its shape and its label', () => {
    const dc = STATES.find((state) => state.code === 'DC')
    const label = dc && labelFor(dc)
    expect(label?.hit).toBeGreaterThan(0)
    if (!dc || !label?.hit) return
    const [x, y] = label.at
    const [ax, ay] = dc.anchor
    expect(Math.hypot(ax - x, ay - y)).toBeLessThan(label.hit)
  })

  it('gives no other state an extra target', () => {
    const others = STATES.filter((state) => state.code !== 'DC' && labelFor(state).hit)
    expect(others).toEqual([])
  })
})
