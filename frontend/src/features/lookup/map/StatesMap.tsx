import { AnimatePresence, motion, useIsPresent, useReducedMotion } from 'motion/react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent, MouseEvent, PointerEvent } from 'react'
import { EASE } from '../../../components/motion.ts'
import { DESKTOP, useMedia } from '../../../lib/media.ts'
import { useSources } from '../../../lib/queries.ts'
import { labelFor, type StateLabel } from './labels.ts'
import { coveredStates } from './mapInfo.ts'
import { StateCard, type MapPoint } from './StateCard.tsx'
import { MAP_HEIGHT, MAP_WIDTH, NATION, STATES, type MapState } from './states.ts'
import './map.css'

/** The slab's depth: copies of the nation, each this many canvas units lower. */
const SLAB_STEP = 1.3
const SLAB = [10, 9, 8, 7, 6, 5, 4, 3, 2, 1] as const
/** The side's shade by depth: light under the states, darkest at the bottom (map.css). */
const slabTone = (step: number) => (step > 7 ? 'deep' : step > 3 ? 'mid' : 'light')
/** How far a hovered or filtered state rises, in canvas units. */
const RISE = 7
/** The ring sits this many label sizes above the abbreviation's centre, clear of the letters. */
const RING_ABOVE = 1.1
/** How the raised block leaves: it descends, and dissolves into the state as it lands. */
const DESCEND = { duration: 0.26, ease: EASE } as const
const DISSOLVE = { duration: 0.16, delay: 0.1 } as const
/** The line's length in pixels. */
const LINE_DESKTOP = 72
const LINE_PHONE = 56
/** Devices whose pointer can hover; elsewhere the filtered state carries the text. */
const HOVER = '(hover: hover)'
/**
 * How long the pointer (or focus) must rest on one state before its ring, line and text show;
 * the rise itself is immediate. A sweep across the map raises states but writes nothing.
 */
const HOVER_INTENT_MS = 180

/** The hovered state, and whether it has been hovered long enough to show its text. */
type Hover = { code: string | null; settled: boolean }
const NO_HOVER: Hover = { code: null, settled: false }

type Placed = MapState & { label: StateLabel }

const PLACED: readonly Placed[] = STATES.map((state) => ({ ...state, label: labelFor(state) }))
const BY_CODE = new Map(PLACED.map((state) => [state.code, state]))
const BY_NAME = [...PLACED].sort((a, b) => a.name.localeCompare(b.name))

/** The state under the pointer, or null; touches are taps, not hovers. */
function stateAt(event: PointerEvent<SVGSVGElement>, target: EventTarget | null): string | null {
  if (event.pointerType === 'touch' || !(target instanceof Element)) return null
  return target.closest('[data-code]')?.getAttribute('data-code') ?? null
}

/**
 * The connected states on a tilted map. Hovering a state raises it in blue with a line and, on
 * top of it, the state's name and total; a click makes it the lookup's state filter, which stays
 * raised until it is clicked again. One SVG on a still CSS 3D plane (map.css), with room kept
 * above it for that text.
 */
export function StatesMap({
  filter,
  onFilter,
}: {
  /** The state the lookup is narrowed to, if any. */
  filter: string | null
  /** A state was chosen: the page toggles its filter. */
  onFilter: (code: string) => void
}) {
  const sources = useSources()
  const covered = useMemo(() => coveredStates(sources.data ?? []), [sources.data])
  const [hover, setHover] = useState<Hover>(NO_HOVER)
  const hovered = hover.code
  const [point, setPoint] = useState<(MapPoint & { code: string }) | null>(null)
  const [focused, setFocused] = useState<string | null>(null)
  const buttons = useRef(new Map<string, HTMLButtonElement>())
  const stage = useRef<HTMLDivElement>(null)
  const dot = useRef<SVGCircleElement>(null)

  const desktop = useMedia(DESKTOP)
  const canHover = useMedia(HOVER)
  const reduced = useReducedMotion() ?? false
  /**
   * The state whose name and total show: the hovered one once it has settled, or on touch the
   * filtered one (at once).
   */
  const shown = (hover.settled ? hovered : null) ?? (canHover ? null : filter)

  /** A new state under the pointer or focus: it rises now and settles after the intent delay. */
  const enter = useCallback((code: string) => {
    setHover((current) => (current.code === code ? current : { code, settled: false }))
  }, [])
  /** The pointer or focus left `code`; anything else hovered meanwhile stays. */
  const leave = useCallback((code: string) => {
    setHover((current) => (current.code === code ? NO_HOVER : current))
  }, [])

  // The intent timer: restarted by every change of hovered state, cleared on unmount.
  useEffect(() => {
    const code = hover.code
    if (code === null || hover.settled) return
    const timer = setTimeout(
      () => setHover((current) => (current.code === code ? { code, settled: true } : current)),
      HOVER_INTENT_MS,
    )
    return () => clearTimeout(timer)
  }, [hover])

  /** The shown state's mark as the browser projects it, in the stage's own pixels. */
  const measure = useCallback(() => {
    const box = stage.current
    const mark = dot.current
    const code = mark?.dataset.code
    if (!box || !mark || !code) return
    // The ring goes above the letters: lift the mark by the label's size at this breakpoint
    // (an SVG font size in px is in canvas units).
    const text = box.querySelector(`[data-label="${code}"]`)
    const size = text ? Number.parseFloat(getComputedStyle(text).fontSize) || 0 : 0
    mark.setAttribute('transform', `translate(0 ${-RING_ABOVE * size})`)
    const outer = box.getBoundingClientRect()
    const inner = mark.getBoundingClientRect()
    // A scaled ancestor would shrink the stage on screen; undo it so the overlay lands in local
    // pixels. The text may reach the stage's own top (its padding is the room kept for it).
    const scale = outer.width ? box.offsetWidth / outer.width : 1
    setPoint({
      code,
      x: (inner.left + inner.width / 2 - outer.left) * scale,
      y: (inner.top + inner.height / 2 - outer.top) * scale,
      width: box.offsetWidth,
      minTop: 0,
    })
  }, [])

  useLayoutEffect(() => {
    if (shown === null) return
    measure()
    const box = stage.current
    if (!box) return
    const observer = new ResizeObserver(measure)
    observer.observe(box)
    return () => observer.disconnect()
  }, [shown, measure])

  const onClick = (event: MouseEvent<SVGSVGElement>) => {
    const target = event.target instanceof Element ? event.target.closest('[data-code]') : null
    const code = target?.getAttribute('data-code')
    if (code) onFilter(code)
  }

  // Delegated hover: entering a state shows it; moving within the same state changes nothing.
  const onPointerOver = (event: PointerEvent<SVGSVGElement>) => {
    const code = stateAt(event, event.target)
    if (code) enter(code)
  }
  const onPointerOut = (event: PointerEvent<SVGSVGElement>) => {
    const from = stateAt(event, event.target)
    if (from !== null && from !== stateAt(event, event.relatedTarget)) {
      leave(from)
    }
  }

  /** One tab stop for the whole map; the arrow keys move between states, A to Z. */
  const onButtonKey = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key]
    const target =
      event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? BY_NAME.length - 1
          : step === undefined
            ? null
            : (index + step + BY_NAME.length) % BY_NAME.length
    if (target === null) return
    event.preventDefault()
    const code = BY_NAME[target]?.code
    if (code) buttons.current.get(code)?.focus()
  }

  const active = shown === null ? undefined : BY_CODE.get(shown)
  const mark = active?.label.at
  const ring = focused === null ? undefined : BY_CODE.get(focused)
  // Raised: the filtered state, then the hovered one on top of it (once if they are the same).
  const raised = [...new Set([filter, hovered])].flatMap((code) => {
    const state = code === null ? undefined : BY_CODE.get(code)
    return state ? [state] : []
  })
  const isRaised = (code: string) => code === filter || code === hovered
  // The roving tab stop: the filtered state, else the first one.
  const stop = filter ?? BY_NAME[0]?.code

  return (
    // Room above the map for the shown state's line and text; at the top a line gets shorter.
    <div ref={stage} className="relative mx-auto flow-root w-full max-w-[1100px] pt-12 lg:pt-16">
      {/* The tilt leaves empty bands above and below the plane's box; the margins take them back. */}
      <div className="map-tilt -mt-[4%] -mb-[2%] lg:-mt-[9%] lg:-mb-[1.5%]">
        <div className="map-plane">
          {/* A picture for the pointer; keyboards and screen readers use the buttons below. */}
          <svg
            viewBox={`0 0 ${MAP_WIDTH} ${MAP_HEIGHT}`}
            aria-hidden="true"
            onClick={onClick}
            onPointerOver={onPointerOver}
            onPointerOut={onPointerOut}
            className="us-map block h-auto w-full select-none"
          >
            <defs>
              <path id="map-nation" d={NATION} />
              <radialGradient id="map-ground">
                <stop offset="0" className="map-ground-stop" stopOpacity="1" />
                <stop offset="0.55" className="map-ground-stop" stopOpacity="0.75" />
                <stop offset="1" className="map-ground-stop" stopOpacity="0" />
              </radialGradient>
            </defs>
            {/* The ground shadow: one static gradient ellipse under the slab, never animated. */}
            <ellipse cx={487} cy={410} rx={480} ry={220} fill="url(#map-ground)" />
            <g className="map-slab" aria-hidden="true">
              {SLAB.map((step) => (
                <use
                  key={step}
                  href="#map-nation"
                  y={step * SLAB_STEP}
                  data-tone={slabTone(step)}
                />
              ))}
            </g>
            <g>
              {PLACED.map((state) => (
                <g
                  key={state.code}
                  className="state"
                  data-code={state.code}
                  data-listed={covered.has(state.code) ? '' : undefined}
                >
                  <path d={state.d} vectorEffect="non-scaling-stroke" />
                </g>
              ))}
            </g>
            {/* Targets for states too small to point at, above every state. */}
            <g>
              {PLACED.flatMap(({ code, label }) =>
                label.hit
                  ? [
                      <circle
                        key={code}
                        className="state-hit"
                        data-code={code}
                        cx={label.at[0]}
                        cy={label.at[1]}
                        r={label.hit}
                      />,
                    ]
                  : [],
              )}
            </g>
            <Labels />
            <AnimatePresence>
              {raised.map((state) => (
                <Raised key={state.code} state={state} reduced={reduced} />
              ))}
            </AnimatePresence>
            {ring && (
              <path
                d={ring.d}
                className="map-focus"
                vectorEffect="non-scaling-stroke"
                transform={isRaised(ring.code) ? `translate(0 ${-RISE})` : undefined}
              />
            )}
            {active && mark && (
              <circle
                ref={dot}
                data-code={active.code}
                cx={mark[0]}
                cy={mark[1] - RISE}
                r={0.5}
                fill="none"
              />
            )}
          </svg>
        </div>
      </div>
      <fieldset className="sr-only">
        <legend>Exclusion lists by state</legend>
        {BY_NAME.map((state, index) => (
          <button
            key={state.code}
            ref={(element) => {
              if (element) buttons.current.set(state.code, element)
              else buttons.current.delete(state.code)
            }}
            type="button"
            tabIndex={state.code === stop ? 0 : -1}
            aria-pressed={state.code === filter}
            onClick={() => onFilter(state.code)}
            onKeyDown={(event) => onButtonKey(event, index)}
            onFocus={(event) => {
              if (!event.currentTarget.matches(':focus-visible')) return
              setFocused(state.code)
              enter(state.code)
            }}
            onBlur={() => {
              setFocused(null)
              leave(state.code)
            }}
          >
            {state.name}
          </button>
        ))}
      </fieldset>
      <div aria-live="polite" className="pointer-events-none absolute inset-0 z-10">
        <AnimatePresence>
          {active && point?.code === active.code && (
            <StateCard
              key={active.code}
              name={active.name}
              code={active.code}
              point={point}
              line={desktop ? LINE_DESKTOP : LINE_PHONE}
              sources={sources}
              reduced={reduced}
            />
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}

const labelClass = (label: StateLabel) => (label.small ? 'map-label map-label-small' : 'map-label')

/** The abbreviations, each on its state, never hidden or moved. Never animated. */
function Labels() {
  return (
    <g pointerEvents="none" aria-hidden="true" textAnchor="middle" dominantBaseline="central">
      {PLACED.map(({ code, label }) => (
        <text
          key={code}
          className={labelClass(label)}
          x={label.at[0]}
          y={label.at[1]}
          data-label={code}
        >
          {code}
        </text>
      ))}
    </g>
  )
}

/**
 * A hovered or filtered state as a block risen above the plane: a darker side under a blue top,
 * carrying a copy of the abbreviation at exactly the base label's place, so the letters rise and
 * land with the block and never jump. On release the block descends and dissolves as it lands.
 * It answers to the pointer as its state (`data-code`), so hovering the risen top keeps it up;
 * while it leaves it lets the pointer through to the states under it.
 */
function Raised({ state, reduced }: { state: Placed; reduced: boolean }) {
  const { at, small } = state.label
  const present = useIsPresent()
  return (
    <motion.g
      aria-hidden="true"
      data-code={present ? state.code : undefined}
      pointerEvents={present ? undefined : 'none'}
      exit={{ opacity: 0, transition: reduced ? { duration: 0 } : DISSOLVE }}
    >
      <path d={state.d} className="map-side" vectorEffect="non-scaling-stroke" />
      <motion.g
        initial={reduced ? false : { y: 0 }}
        animate={{ y: -RISE }}
        exit={reduced ? undefined : { y: 0 }}
        transition={DESCEND}
      >
        <path d={state.d} className="map-top" vectorEffect="non-scaling-stroke" />
        {!small && (
          <text
            className={`${labelClass(state.label)} map-top-label`}
            x={at[0]}
            y={at[1]}
            textAnchor="middle"
            dominantBaseline="central"
            pointerEvents="none"
          >
            {state.code}
          </text>
        )}
      </motion.g>
    </motion.g>
  )
}
