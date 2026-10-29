/**
 * Hand-tuned label positions for the states map, in canvas units (975 × 610). A centroid is a
 * good label point for most states; these fix the ones where it is not, and place the small
 * north-eastern states' labels on the states themselves, in a smaller size so none collide.
 */
import type { MapState } from './states.ts'

type Point = readonly [number, number]

/** Moves a label from the state's centroid by (dx, dy). */
const NUDGES: Readonly<Record<string, Point>> = {
  AK: [8, -4],
  FL: [16, 6],
  HI: [-36, -24],
  ID: [2, 18],
  KY: [6, 2],
  LA: [-10, -4],
  MI: [10, 16],
  CA: [-6, 6],
  MN: [-4, 6],
  NY: [4, 4],
  VA: [6, 2],
  WV: [-2, 4],
  OK: [8, 0],
  TN: [0, 1],
  SC: [2, 0],
}

/** Where the small north-eastern states' labels sit, on the state; drawn in the small size. */
const SMALL: Readonly<Record<string, Point>> = {
  VT: [867, 123],
  NH: [893, 133],
  MA: [885, 166],
  RI: [906, 185],
  CT: [884, 194],
  NJ: [862, 234],
  DE: [856, 266],
  MD: [814, 255],
  DC: [830, 270],
}

/**
 * States drawn too small to point at: the radius, in canvas units, of an invisible target around
 * the label, so the label itself can be hovered and clicked.
 */
const HIT: Readonly<Record<string, number>> = { DC: 7 }

/**
 * Where a state's abbreviation goes; `small` draws it at 70 % of the label size (85 % on
 * desktop); `hit` is the radius of an extra pointer target around it.
 */
export type StateLabel = { at: Point; small?: true; hit?: number }

export function labelFor(state: Pick<MapState, 'code' | 'anchor'>): StateLabel {
  const small = SMALL[state.code]
  const hit = HIT[state.code]
  if (small) return hit ? { at: small, small: true, hit } : { at: small, small: true }
  const [x, y] = state.anchor
  const [dx, dy] = NUDGES[state.code] ?? [0, 0]
  return { at: [x + dx, y + dy] }
}
