import { Draw, Svg, type IllustrationProps } from './Svg.tsx'

export function EmptyProviders({ size = 120, ...props }: IllustrationProps) {
  return (
    <Svg box={120} tone="ill-muted" size={size} {...props}>
      <Draw d="M24 34h56a12 12 0 0 1 12 12v40a12 12 0 0 1-12 12H24a12 12 0 0 1-12-12V46a12 12 0 0 1 12-12z" />
      <Draw d="M44 60a8 8 0 1 1-16 0a8 8 0 1 1 16 0" i={1} />
      <Draw d="M22 86a14 14 0 0 1 28 0" i={2} />
      <Draw d="M60 58h20M60 70h12" i={3} />
      <Draw d="M104 12v20M94 22h20" i={4} accent />
    </Svg>
  )
}

export function EmptySources({ size = 120, ...props }: IllustrationProps) {
  return (
    <Svg box={120} tone="ill-muted" size={size} {...props}>
      <Draw d="M30 36a30 10 0 0 0 60 0a30 10 0 0 0-60 0" />
      <Draw d="M30 36v48a30 10 0 0 0 60 0V36" i={1} />
      <Draw d="M30 60a30 10 0 0 0 60 0" i={2} />
      <Draw d="M108 18a5 5 0 1 1-10 0a5 5 0 1 1 10 0" i={3} accent />
    </Svg>
  )
}
