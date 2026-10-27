import type { CSSProperties, ReactNode } from 'react'

export type IllustrationProps = { size?: number; className?: string; title?: string }

type SvgProps = IllustrationProps & { box?: 24 | 120; tone?: string; children: ReactNode }

export function Svg({ size = 24, box = 24, className, title, tone, children }: SvgProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${box} ${box}`}
      fill="none"
      stroke="currentColor"
      strokeWidth={2.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={title ? 'img' : undefined}
      aria-hidden={title ? undefined : true}
      focusable="false"
      className={[tone, className].filter(Boolean).join(' ') || undefined}
    >
      {title ? <title>{title}</title> : null}
      {children}
    </svg>
  )
}

type DrawProps = { d: string; i?: number; accent?: boolean }

export function Draw({ d, i = 0, accent = false }: DrawProps) {
  return (
    <path
      d={d}
      pathLength={1}
      className={accent ? 'ill-draw ill-accent' : 'ill-draw'}
      style={i ? ({ '--i': i } as CSSProperties) : undefined}
    />
  )
}
