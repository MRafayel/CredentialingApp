import { Draw, Svg, type IllustrationProps } from './Svg.tsx'

export function PersonMark(props: IllustrationProps) {
  return (
    <Svg {...props}>
      <Draw d="M16 8a4 4 0 1 1-8 0a4 4 0 1 1 8 0" />
      <Draw d="M4.5 20.5a7.5 7.5 0 0 1 15 0" i={1} />
    </Svg>
  )
}

export function OrganizationMark(props: IllustrationProps) {
  return (
    <Svg {...props}>
      <Draw d="M2.5 21h19M4.5 21V8.5L12 3.5l7.5 5V21" />
      <Draw d="M10 21v-4h4v4" i={1} />
      <Draw d="M12 8v4M10 10h4" i={2} />
    </Svg>
  )
}
