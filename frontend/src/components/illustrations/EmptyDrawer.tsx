import type { IllustrationProps } from './Svg.tsx'

const PULL = 'M259.8 428.5v7l60.6 35v-7'
const TOP_PULL = 'M265 262.5v7l60.6 35v-7'

/**
 * "No matches": an isometric filing cabinet whose bottom drawer slides open while a magnifier
 * fades in, scans the empty tray and fades out as the drawer closes (`ill-drawer`, `ill-lens` in
 * illustrations.css, 3.4 s loop with a rest while closed). Under reduced motion it holds the open drawer with the magnifier
 * in view. `size` is the height; strokes are `currentColor`, muted unless the caller sets one.
 */
export function EmptyDrawer({ size = 160, className, title }: IllustrationProps) {
  return (
    <svg
      width={Math.round((size * 464) / 581)}
      height={size}
      viewBox="130 21 464 581"
      fill="none"
      stroke="currentColor"
      strokeWidth={4.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={title ? 'img' : undefined}
      aria-hidden={title ? undefined : true}
      focusable="false"
      className={['ill-muted', className].filter(Boolean).join(' ')}
    >
      {title ? <title>{title}</title> : null}
      <path
        className="fill-surface"
        d="M204.3 472.5l21.7 12.5v12l-21.7-12.5zM364.5 565l21.7 12.5v12L364.5 577z"
      />
      <path
        className="fill-soft"
        d="M386.2 577.5l21.6-12.5v12l-21.6 12.5zM563.7 475l21.7-12.5v12L563.7 487z"
      />
      <path className="fill-surface" d="M200 470l190.5 110V250L200 140z" />
      <path className="fill-line-strong" d="M208.7 455l173.2 100V420L208.7 320z" />
      <g className="ill-drawer">
        <path
          className="fill-lift"
          d="M388.8 351l159.3 92V331L388.8 239zM215.6 451l173.2-100V239L215.6 339z"
        />
        <path className="fill-soft" d="M215.6 451l159.3 92 173.2-100-159.3-92z" />
        <path className="fill-surface" d="M374.9 543l173.2-100V331L374.9 431z" />
        <path d="M374.9 501l173.2-100M374.9 509l173.2-100" />
        <g fill="currentColor" stroke="none">
          <circle cx="410" cy="489" r="3" />
          <circle cx="470" cy="455" r="3" />
        </g>
        <path className="fill-soft" d="M203.5 323l173.2 100 5.2-3-173.2-100z" />
        <path className="fill-lift" d="M376.7 558l5.2-3V420l-5.2 3z" />
        <path
          className="fill-surface"
          d="M203.5 458l173.2 100V423L203.5 323zM272.7 413l34.7 20v-20l-34.7-20z"
        />
        <path className="fill-soft" d="M276.2 411l27.7 16v-12l-27.7-16z" />
        <path strokeWidth={13} d={PULL} />
        <path className="stroke-surface" strokeWidth={4} d={PULL} />
      </g>
      <path
        className="fill-surface"
        d="M200 315l190.5 110V250L200 140zM381.9 575l8.6 5V425l-8.6-5zM208.7 305l173.2 100V270L208.7 170zM277.9 245l34.7 20v-20l-34.7-20z"
      />
      <path
        className="fill-soft"
        d="M281.4 243l27.7 16v-12l-27.7-16zM390.5 580l199.2-115V135L390.5 250z"
      />
      <circle className="fill-soft" cx="377.5" cy="260.5" r="4.5" />
      <path strokeWidth={13} d={TOP_PULL} />
      <path className="stroke-surface" strokeWidth={4} d={TOP_PULL} />
      <path className="fill-surface" d="M200 140l190.5 110 199.2-115L399.2 25z" />
      <path d="M200 152l190.5 110 199.2-115" />
      <g className="ill-lens">
        <g transform="translate(222 372) rotate(-6) scale(1.25)">
          <path strokeWidth={7} d="M27 27l15 15" />
          <path strokeWidth={16} d="M44 44l38 38" />
          <path className="stroke-surface" strokeWidth={9} d="M44 44l38 38" />
          <circle r="40" className="fill-surface" fillOpacity={0.7} strokeWidth={8} />
          <path strokeWidth={3.6} strokeOpacity={0.5} d="M-24-12a27 27 0 0 1 14-13" />
        </g>
      </g>
    </svg>
  )
}
