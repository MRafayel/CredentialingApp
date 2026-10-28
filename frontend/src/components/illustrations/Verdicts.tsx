import { CHECK, SHIELD } from './paths.ts'
import { Draw, Svg, type IllustrationProps } from './Svg.tsx'

export function VerdictClear(props: IllustrationProps) {
  return (
    <Svg tone="ill-clear" {...props}>
      <Draw d={SHIELD} />
      <Draw d={CHECK} i={1} />
    </Svg>
  )
}

export function VerdictListed(props: IllustrationProps) {
  return (
    <Svg tone="ill-listed" {...props}>
      <Draw d="M8.3 3h7.4L21 8.3v7.4L15.7 21H8.3L3 15.7V8.3z" />
      <Draw d="M8 12h8" i={1} />
    </Svg>
  )
}

export function VerdictReview(props: IllustrationProps) {
  return (
    <Svg tone="ill-review" {...props}>
      <Draw d="M10.3 4.2a2 2 0 0 1 3.4 0l7.6 13.2a2 2 0 0 1-1.7 3H4.4a2 2 0 0 1-1.7-3z" />
      <Draw d="M12 9.5v4" i={1} />
      <Draw d="M12 17h.01" i={2} />
    </Svg>
  )
}

export function VerdictFlagged(props: IllustrationProps) {
  return (
    <Svg tone="ill-flagged" {...props}>
      <Draw d="M5 21V3.5" />
      <Draw d="M5 4.5h12.5l-3 4.25l3 4.25H5" i={1} />
    </Svg>
  )
}

export function VerdictBlocked(props: IllustrationProps) {
  return (
    <Svg tone="ill-blocked" {...props}>
      <Draw d="M21 12a9 9 0 1 1-18 0a9 9 0 1 1 18 0" />
      <Draw d="M8 12h8" i={1} />
    </Svg>
  )
}

export function VerdictUnchecked(props: IllustrationProps) {
  return (
    <Svg tone="ill-unchecked" {...props}>
      <circle cx="12" cy="12" r="9" strokeDasharray="0 4.04" className="ill-fade" />
    </Svg>
  )
}
