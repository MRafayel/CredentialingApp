/**
 * Scroll-linked values for the bars and the receding hero. Everything is proportional to the
 * scroll position (no thresholds), so glass and the hero fade in and out with no pop. Under
 * reduced motion the bars' glass follows the scroll directly (no spring) and the hero stays still.
 */
import {
  useReducedMotion,
  useScroll,
  useSpring,
  useTransform,
  type MotionValue,
} from 'motion/react'
import { useLayoutEffect, useState } from 'react'

/** Calm smoothing: follows the scroll closely, never overshoots. */
const SMOOTH = { stiffness: 120, damping: 24 } as const

/** How far the page scrolls before a top bar's glass is fully in. */
const BAR_GLASS_DISTANCE = 80

/** 0 at the top of the page, 1 once `distance` px have scrolled, smoothed unless reduced. */
function useScrollProgress(distance: number): MotionValue<number> {
  const reduced = useReducedMotion() ?? false
  const { scrollY } = useScroll()
  const linear = useTransform(scrollY, [0, Math.max(distance, 1)], [0, 1])
  const smooth = useSpring(linear, SMOOTH)
  return reduced ? linear : smooth
}

/** A top bar's glass opacity: 0 → 1 over the first 80 px of scroll. */
export function useBarGlass(): MotionValue<number> {
  return useScrollProgress(BAR_GLASS_DISTANCE)
}

/** An element's top in the document, from layout alone (transforms and scroll do not count). */
function layoutTop(element: HTMLElement): number {
  let top = 0
  for (let node: Element | null = element; node instanceof HTMLElement; node = node.offsetParent) {
    top += node.offsetTop
  }
  return top
}

/**
 * Where a page's content starts to pass under the floating nav: the nav's bottom edge, read from
 * `PageBody`'s top padding, which is that edge plus 40 px on every viewport (safe area included).
 */
function navBottom(element: HTMLElement): number {
  const main = element.closest('main')
  if (!main) return 0
  return (Number.parseFloat(getComputedStyle(main).paddingTop) || 0) - 40
}

/**
 * The receding hero (Lookup, Providers): as the page scrolls, the title and then the search field
 * scale down a little (1 → 0.96) and fade out, passing under the nav as if into the back, while
 * the results rise 12 px towards the reader. One progress drives it all, from the top of the page
 * until the field's top meets the nav's bottom edge; `anchor` is the field's wrapper, measured
 * from layout alone so its own transform does not move the target. Under reduced motion nothing
 * scales, fades or moves: the hero simply scrolls under the nav.
 */
type RecedingHero = {
  /** A callback ref for the element that wraps the search field. */
  anchor: (element: HTMLElement | null) => void
  title: { scale: MotionValue<number> | number; opacity: MotionValue<number> | number }
  field: { scale: MotionValue<number> | number; opacity: MotionValue<number> | number }
  results: { y: MotionValue<number> | number }
}

/** Reduced motion: the hero keeps its size and opacity. */
const STILL = { scale: 1, opacity: 1 } as const

export function useRecedingHero(): RecedingHero {
  const [field, anchor] = useState<HTMLElement | null>(null)
  const [distance, setDistance] = useState(160)
  const reduced = useReducedMotion() ?? false

  useLayoutEffect(() => {
    if (!field) return
    const measure = () => setDistance(Math.max(1, Math.round(layoutTop(field) - navBottom(field))))
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(document.body)
    return () => observer.disconnect()
  }, [field])

  const progress = useScrollProgress(distance)
  const scale = useTransform(progress, [0, 1], [1, 0.96])
  const titleOpacity = useTransform(progress, [0, 0.85], [1, 0])
  // The field stays crisp for the first quarter, then fades out as it reaches the nav.
  const fieldOpacity = useTransform(progress, [0.25, 1], [1, 0])
  const resultsY = useTransform(progress, [0, 1], [0, -12])

  if (reduced) {
    return { anchor, title: STILL, field: STILL, results: { y: 0 } }
  }
  return {
    anchor,
    title: { scale, opacity: titleOpacity },
    field: { scale, opacity: fieldOpacity },
    results: { y: resultsY },
  }
}
