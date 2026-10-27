/** The motion vocabulary: one ease, a few durations, shared variants. Calm, never bouncy. */
import type { Transition, Variants } from 'motion/react'

export const EASE = [0.22, 1, 0.36, 1] as const
const GLIDE = [0.4, 0, 0.2, 1] as const

export const quick: Transition = { duration: 0.18, ease: EASE }
/** A page or a piece arriving. */
export const settle: Transition = { duration: 0.26, ease: EASE }
/** A selection moving: the nav indicator, the tab lens. */
export const glide: Transition = { duration: 0.3, ease: GLIDE }
/** A sheet sliding in or out. */
export const slide: Transition = { duration: 0.32, ease: EASE }

/** A container whose children rise in one after another. */
export const stagger = (gap = 0.035): Variants => ({
  hidden: {},
  shown: { transition: { staggerChildren: gap } },
})

export const rise: Variants = {
  hidden: { opacity: 0, y: 10 },
  shown: { opacity: 1, y: 0, transition: settle },
}

export const press = { scale: 0.98 }
