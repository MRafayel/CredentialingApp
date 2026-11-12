/** Small line icons for controls; drawings that carry a state live in ./illustrations. */

const PATHS = {
  search: 'M10.5 17a6.5 6.5 0 1 0 0-13a6.5 6.5 0 0 0 0 13zM15.5 15.5L20 20',
  plus: 'M12 5v14M5 12h14',
  chevronDown: 'M6 9l6 6l6-6',
  /** The roster's filter menu: two sliders, the top knob right, the bottom knob left. */
  sliders:
    'M3 8.5h9.5M18.5 8.5H21M3 15.5h2.5M12 15.5h9M15.5 5.5a3 3 0 1 1 0 6a3 3 0 0 1 0-6zM8.5 12.5a3 3 0 1 1 0 6a3 3 0 0 1 0-6z',
  chevronRight: 'M9 6l6 6l-6 6',
  chevronLeft: 'M15 6l-6 6l6 6',
  arrowLeft: 'M19 12H5M11 6l-6 6l6 6',
  external: 'M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5',
  copy: 'M9 9h10v10H9zM5 15V5h10',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  close: 'M6 6l12 12M18 6L6 18',
  providers:
    'M9 11a3.5 3.5 0 1 0 0-7a3.5 3.5 0 0 0 0 7zM2.5 20a6.5 6.5 0 0 1 13 0M16 4.5a3.5 3.5 0 0 1 0 6.5M18.5 20a6.5 6.5 0 0 0-3-5.5',
  sources:
    'M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3s-8-1.3-8-3s3.6-3 8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
  refresh: 'M20 11a8 8 0 0 0-14.6-4.5M4 4v4h4M4 13a8 8 0 0 0 14.6 4.5M20 20v-4h-4',
  trash: 'M5 7h14M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3',
  /** A live request to a registry. */
  bolt: 'M13 2.5L5 13.5h6l-1 8l8-11h-6l1-8z',
  /** The NPI registry: an identity card. */
  registry:
    'M3 6.5A1.5 1.5 0 0 1 4.5 5h15A1.5 1.5 0 0 1 21 6.5v11a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5zM8.5 12a2 2 0 1 0 0-4a2 2 0 0 0 0 4zM5.5 16a3 3 0 0 1 6 0M14 9h4M14 12.5h4M14 16h2.5',
} as const

export type IconName = keyof typeof PATHS

export function Icon({
  name,
  size = 18,
  className,
}: {
  name: IconName
  size?: number
  className?: string
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      className={className}
    >
      <path d={PATHS[name]} />
    </svg>
  )
}
