import { Draw, Svg, type IllustrationProps } from './Svg.tsx'

/**
 * A document being scanned: the sheet draws in once, then an accent line with a soft glow sweeps
 * down it and back for as long as the mark is shown. The one looping mark for work under way on
 * a person (the import note while the first check runs); it leaves with the work. Under reduced
 * motion the line rests mid-sheet.
 */
export function ScanningDocument(props: IllustrationProps) {
  return (
    <Svg {...props}>
      <Draw d="M6 3h8l5 5v13H6z" />
      <Draw d="M14 3v5h5" i={1} />
      <Draw d="M9.5 13h5M9.5 16.5h3" i={2} />
      <g className="ill-scan">
        <path d="M4 8h16" className="ill-accent" strokeWidth={6} opacity={0.18} />
        <path d="M4 8h16" className="ill-accent" />
      </g>
    </Svg>
  )
}
