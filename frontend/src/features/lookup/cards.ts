/**
 * A record card on --shadow-card that opens its record: it lifts a pixel on hover and presses
 * to 0.99, like a roster row. The name's link (`CARD_LINK`) covers the card; buttons inside it
 * sit above that cover (`relative z-10`).
 */
export const CARD =
  'relative rounded-item bg-surface shadow-card transition-[box-shadow,translate,scale] duration-180 ease-out-soft hover:shadow-card-hover active:scale-[0.99] motion-safe:hover:-translate-y-px'

/** The record link's cover: the whole card is the target, and the focus ring traces the card. */
export const CARD_LINK =
  'outline-none after:absolute after:inset-0 after:rounded-item focus-visible:after:outline-2 focus-visible:after:outline-offset-2 focus-visible:after:outline-accent'
