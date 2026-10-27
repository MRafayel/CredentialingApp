import { CHECK, SHIELD } from './paths.ts'
import { Draw, Svg, type IllustrationProps } from './Svg.tsx'

type WordmarkProps = IllustrationProps & { label?: string }

export function Wordmark({ size = 24, className, title, label }: WordmarkProps) {
  const mark = (
    <Svg size={size} className={label ? undefined : className} title={label ? undefined : title}>
      <Draw d={SHIELD} />
      <Draw d={CHECK} i={1} />
    </Svg>
  )
  if (!label) return mark
  return (
    <span
      className={['inline-flex items-center gap-2 font-semibold tracking-tight', className]
        .filter(Boolean)
        .join(' ')}
    >
      {mark}
      <span>{label}</span>
    </span>
  )
}
