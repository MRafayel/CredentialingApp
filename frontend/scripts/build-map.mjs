/**
 * Builds the Lookup page's states map (src/features/lookup/map/states.ts) from us-atlas.
 *
 * Data: https://cdn.jsdelivr.net/npm/us-atlas@3.0.1/states-albers-10m.json, a TopoJSON already
 * projected (Albers USA, Alaska and Hawaii inset) onto a 975 × 610 canvas. us-atlas derives it
 * from the U.S. Census Bureau's cartographic boundary files, which are in the public domain.
 *
 * Run from frontend/ after downloading the file into a folder of its own:
 *   node scripts/build-map.mjs /path/to/states-albers-10m.json
 *
 * No dependencies. Shared borders are simplified once per TopoJSON arc (Douglas–Peucker, the
 * arc's ends fixed), so neighbouring states keep meeting exactly; tiny islands and the islands
 * west of the canvas's left edge are dropped.
 */
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const TOLERANCE = 0.8 // canvas units
const MIN_RING_AREA = 4 // canvas units², unless it is the state's largest ring
const OUT = fileURLToPath(new URL('../src/features/lookup/map/states.ts', import.meta.url))

const CODES = {
  '01': 'AL',
  '02': 'AK',
  '04': 'AZ',
  '05': 'AR',
  '06': 'CA',
  '08': 'CO',
  '09': 'CT',
  10: 'DE',
  11: 'DC',
  12: 'FL',
  13: 'GA',
  15: 'HI',
  16: 'ID',
  17: 'IL',
  18: 'IN',
  19: 'IA',
  20: 'KS',
  21: 'KY',
  22: 'LA',
  23: 'ME',
  24: 'MD',
  25: 'MA',
  26: 'MI',
  27: 'MN',
  28: 'MS',
  29: 'MO',
  30: 'MT',
  31: 'NE',
  32: 'NV',
  33: 'NH',
  34: 'NJ',
  35: 'NM',
  36: 'NY',
  37: 'NC',
  38: 'ND',
  39: 'OH',
  40: 'OK',
  41: 'OR',
  42: 'PA',
  44: 'RI',
  45: 'SC',
  46: 'SD',
  47: 'TN',
  48: 'TX',
  49: 'UT',
  50: 'VT',
  51: 'VA',
  53: 'WA',
  54: 'WV',
  55: 'WI',
  56: 'WY',
}

const input = process.argv[2]
if (!input) {
  console.error('usage: node scripts/build-map.mjs <states-albers-10m.json>')
  process.exit(1)
}
const topology = JSON.parse(readFileSync(input, 'utf8'))
const { scale, translate } = topology.transform

// Decode: quantized, delta-encoded arcs → absolute canvas points.
const decoded = topology.arcs.map((arc) => {
  let x = 0
  let y = 0
  return arc.map(([dx, dy]) => {
    x += dx
    y += dy
    return [x * scale[0] + translate[0], y * scale[1] + translate[1]]
  })
})

function segmentDistance([px, py], [ax, ay], [bx, by]) {
  const dx = bx - ax
  const dy = by - ay
  const length = dx * dx + dy * dy
  const t = length ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / length)) : 0
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy))
}

function douglasPeucker(points) {
  if (points.length < 3) return points
  const keep = new Uint8Array(points.length)
  keep[0] = keep[points.length - 1] = 1
  const stack = [[0, points.length - 1]]
  while (stack.length) {
    const [first, last] = stack.pop()
    let worst = 0
    let index = -1
    for (let i = first + 1; i < last; i++) {
      const d = segmentDistance(points[i], points[first], points[last])
      if (d > worst) [worst, index] = [d, i]
    }
    if (index !== -1 && worst > TOLERANCE) {
      keep[index] = 1
      stack.push([first, index], [index, last])
    }
  }
  return points.filter((_, i) => keep[i])
}

/** A closed arc (an island) is split at its farthest point so both halves simplify. */
function simplifyArc(points) {
  const [start] = points
  const end = points[points.length - 1]
  if (points.length > 3 && start[0] === end[0] && start[1] === end[1]) {
    let far = 1
    let best = 0
    points.forEach((p, i) => {
      const d = Math.hypot(p[0] - start[0], p[1] - start[1])
      if (d > best) [best, far] = [d, i]
    })
    return [
      ...douglasPeucker(points.slice(0, far + 1)),
      ...douglasPeucker(points.slice(far)).slice(1),
    ]
  }
  return douglasPeucker(points)
}

const arcs = decoded.map(simplifyArc)

function ring(indexes) {
  const points = []
  for (const index of indexes) {
    const arc = index < 0 ? [...arcs[~index]].reverse() : arcs[index]
    points.push(...(points.length ? arc.slice(1) : arc))
  }
  return points
}

function signedArea(points) {
  let sum = 0
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    sum += points[j][0] * points[i][1] - points[i][0] * points[j][1]
  }
  return sum / 2
}

function centroid(points) {
  const area = signedArea(points)
  let x = 0
  let y = 0
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    const cross = points[j][0] * points[i][1] - points[i][0] * points[j][1]
    x += (points[j][0] + points[i][0]) * cross
    y += (points[j][1] + points[i][1]) * cross
  }
  return [round(x / (6 * area)), round(y / (6 * area))]
}

const round = (value) => Math.round(value * 10) / 10
const num = (value) => {
  const text = String(round(value) || 0)
  return text.replace(/^(-?)0\./, '$1.')
}
/** Numbers joined the short way: a minus sign or a leading dot needs no space before it. */
function join(values) {
  let out = ''
  for (const value of values.map(num)) {
    const separator =
      !out || value.startsWith('-') || (value.startsWith('.') && /\.\d*$/.test(out)) ? '' : ' '
    out += separator + value
  }
  return out
}

function absolutePath(points) {
  const [first, ...rest] = points
  return `M${join(first)}L${join(rest.flat())}Z`
}

function relativePath(points) {
  const rounded = points.map(([x, y]) => [round(x), round(y)])
  const [first, ...rest] = rounded
  const deltas = rest.flatMap((p, i) => [p[0] - rounded[i][0], p[1] - rounded[i][1]])
  return `M${join(first)}l${join(deltas)}z`
}

/** Whether a ring has at least one point on the canvas (the far Aleutians fall off its left edge). */
const onCanvas = (points) => points.some(([x]) => x >= 0)

/** Polygons → rings (closing point dropped; tiny or off-canvas islands removed) and the largest. */
function rings(geometry) {
  const polygons = geometry.type === 'Polygon' ? [geometry.arcs] : geometry.arcs
  const all = polygons
    .flat()
    .map((indexes) => ring(indexes).slice(0, -1))
    .filter((points) => points.length >= 3)
    .map((points) => ({ points, area: Math.abs(signedArea(points)) }))
  const largest = all.reduce((a, b) => (b.area > a.area ? b : a))
  return {
    largest: largest.points,
    kept: all.filter((r) => r === largest || (r.area >= MIN_RING_AREA && onCanvas(r.points))),
  }
}

function path(kept) {
  const pick = (points) => {
    const a = absolutePath(points)
    const r = relativePath(points)
    return r.length < a.length ? r : a
  }
  return kept.map(({ points }) => pick(points)).join('')
}

const states = topology.objects.states.geometries
  .map((geometry) => {
    const code = CODES[geometry.id]
    if (!code) throw new Error(`no code for FIPS ${geometry.id}`)
    const { largest, kept } = rings(geometry)
    return { code, name: geometry.properties.name, d: path(kept), anchor: centroid(largest) }
  })
  .sort((a, b) => a.code.localeCompare(b.code))

// The nation outline is every arc used by exactly one state, which us-atlas ships as `nation`.
const nation = path(rings(topology.objects.nation.geometries[0]).kept)

const body = `/**
 * The U.S. states on a 975 × 610 canvas (Albers USA, Alaska and Hawaii inset), simplified.
 * Generated by scripts/build-map.mjs from us-atlas (U.S. Census Bureau cartographic boundary
 * files, public domain). Do not edit by hand; run the script again.
 */

export type MapState = {
  code: string
  name: string
  /** SVG path in canvas units. */
  d: string
  /** Centroid of the state's largest ring. */
  anchor: readonly [number, number]
}

export const MAP_WIDTH = 975
export const MAP_HEIGHT = 610

export const STATES: readonly MapState[] = [
${states
  .map(
    (s) =>
      `  {\n    code: '${s.code}',\n    name: '${s.name}',\n    anchor: [${s.anchor.join(', ')}],\n    d: '${s.d}',\n  },`,
  )
  .join('\n')}
]

/** The country's outline: the slab under the states. */
export const NATION =
  '${nation}'
`
writeFileSync(OUT, body)
console.log(`${states.length} states, ${(Buffer.byteLength(body) / 1024).toFixed(1)} KB → ${OUT}`)
