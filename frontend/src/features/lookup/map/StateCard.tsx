import type { UseQueryResult } from '@tanstack/react-query'
import { motion } from 'motion/react'
import { useLayoutEffect, useRef, useState } from 'react'
import { EASE, settle } from '../../../components/motion.ts'
import { Skeleton } from '../../../components/Skeleton.tsx'
import type { SourceStatus } from '../../../lib/api.ts'
import { hasFetched, totalExcluded } from './mapInfo.ts'
import { placeLabel } from './placement.ts'

/**
 * The shown state's mark, projected, in the map stage's pixels, with the stage's width and the
 * highest the text may reach (the stage's top) in the same pixels.
 */
export type MapPoint = { x: number; y: number; width: number; minTop: number }

type Size = { width: number; height: number }

const RING = 10
/** Ring, line and text fade out together as the raised block dissolves (StatesMap's Raised). */
const FADE = { duration: 0.16, delay: 0.1 } as const
/** Both lines: spaced capitals in the light blue (AA on the page and on a white state). */
const CAPS = 'font-semibold tracking-[0.08em] text-accent-light uppercase'

/**
 * Over the map, outside its tilted plane so text stays crisp: a ring at the shown state, a
 * line rising from it and, on top of the line, the state's name and how many people its own
 * lists exclude. Plain text, no frame.
 */
export function StateCard({
  name,
  code,
  point,
  line,
  sources,
  reduced,
}: {
  name: string
  code: string
  point: MapPoint
  line: number
  sources: UseQueryResult<SourceStatus[]>
  reduced: boolean
}) {
  const block = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState<Size | null>(null)

  useLayoutEffect(() => {
    const element = block.current
    if (!element) return
    const read = () => setSize({ width: element.offsetWidth, height: element.offsetHeight })
    read()
    const observer = new ResizeObserver(read)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  const place = size
    ? placeLabel({ anchor: point, block: size, width: point.width, line, minTop: point.minTop })
    : null

  return (
    <>
      <motion.span
        aria-hidden="true"
        className="absolute rounded-full border-2 border-accent-light bg-surface shadow-[0_0_0_1px_var(--accent-strong)]"
        style={{
          left: point.x - RING / 2,
          top: point.y - RING / 2,
          width: RING,
          height: RING,
        }}
        initial={reduced ? false : { opacity: 0, scale: 0.4 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, transition: reduced ? { duration: 0 } : FADE }}
        transition={{ duration: 0.2, ease: EASE, delay: reduced ? 0 : 0.12 }}
      />
      {place && (
        <motion.span
          aria-hidden="true"
          className="absolute w-0.5 bg-accent-light"
          style={{
            left: point.x - 1,
            top: point.y - place.line,
            height: place.line - RING / 2,
            originY: 1,
          }}
          initial={reduced ? false : { scaleY: 0 }}
          animate={{ scaleY: 1 }}
          exit={{ opacity: 0, transition: reduced ? { duration: 0 } : FADE }}
          transition={{ duration: 0.24, ease: EASE, delay: reduced ? 0 : 0.2 }}
        />
      )}
      <motion.div
        ref={block}
        className="map-callout absolute w-max max-w-[calc(100%-32px)] text-center"
        style={{
          left: place?.left ?? 0,
          top: place?.top ?? 0,
          visibility: place ? 'visible' : 'hidden',
        }}
        initial={reduced ? false : { opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, transition: reduced ? { duration: 0 } : FADE }}
        transition={{ ...settle, delay: reduced ? 0 : 0.36 }}
      >
        <p className={`text-caption lg:text-small ${CAPS}`}>{name}</p>
        <Total code={code} sources={sources} />
      </motion.div>
    </>
  )
}

/** The state's total, or why there is none; a state without a list never reads as zero. */
function Total({ code, sources }: { code: string; sources: UseQueryResult<SourceStatus[]> }) {
  if (sources.isPending) {
    return (
      <div className="mt-1 flex justify-center" aria-label="Loading the total">
        <Skeleton className="h-3.5 w-28" />
      </div>
    )
  }
  if (!sources.data) return null
  const total = totalExcluded(sources.data, code)
  const text =
    total === null ? (
      'No list connected yet'
    ) : !hasFetched(sources.data, code) ? (
      'Not fetched yet'
    ) : (
      <>
        Total excluded <span className="tabular-nums">{new Intl.NumberFormat().format(total)}</span>
      </>
    )
  return <p className={`mt-0.5 text-caption lg:text-small ${CAPS}`}>{text}</p>
}
