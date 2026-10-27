/**
 * The page atmosphere: soft blobs of the blue family fixed behind every page, fading into the
 * page colour. Two layers drift very slowly in opposite directions (CSS, transform only, off
 * under reduced motion). Pure decoration: it never carries meaning, never sits on a card and
 * is kept light enough behind the content column for ink-muted to stay AA.
 */
export function Atmosphere() {
  return (
    <div aria-hidden="true" className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      <div className="absolute -inset-[6%] atmosphere-back" />
      <div className="absolute -inset-[6%] atmosphere-front" />
    </div>
  )
}
