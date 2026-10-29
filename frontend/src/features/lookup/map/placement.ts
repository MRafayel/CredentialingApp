/**
 * Where the shown state's name goes, in the map's own pixels: on top of the line that rises
 * from the state, centred on it. Near the top, where the text would leave the room kept above
 * the map, the line gets shorter instead (never below `minLine`); only the text's centre may
 * shift sideways to stay inside the stage (the line stays at the state).
 */

type Point = { x: number; y: number }
type Size = { width: number; height: number }

export type PlacementInput = {
  anchor: Point
  /** The text block's size. */
  block: Size
  /** The stage's width; the block stays `margin` inside it. */
  width: number
  /** The line's full length. */
  line: number
  /** The highest the block's top may go, in stage pixels (the stage's own top). */
  minTop: number
  /** The shortest line still worth drawing. */
  minLine?: number
  /** Between the line's top and the block's bottom. */
  gap?: number
  margin?: number
}

/** The text block's top-left corner, and the length of the line under it. */
export type LabelPlacement = { left: number; top: number; line: number }

export function placeLabel({
  anchor,
  block,
  width,
  line,
  minTop,
  minLine = 24,
  gap = 8,
  margin = 16,
}: PlacementInput): LabelPlacement {
  const centred = anchor.x - block.width / 2
  const left = Math.min(Math.max(centred, margin), Math.max(margin, width - margin - block.width))
  const above = (length: number) => anchor.y - length - gap - block.height

  if (above(line) >= minTop) return { left, top: above(line), line }
  // Shorten the line so the text sits at minTop; past the floor, the text stays at minTop.
  const fitted = Math.max(minLine, anchor.y - gap - block.height - minTop)
  return { left, top: minTop, line: Math.min(line, fitted) }
}
