import { motion, useReducedMotion, type Transition } from 'motion/react'
import type { ReactNode } from 'react'
import { ButtonLink } from '../../components/Button.tsx'
import { EASE, settle } from '../../components/motion.ts'
import { PageBar, PageBody } from '../../components/PageBar.tsx'

/*
 * The arrival, composed (under 1.4 s): the left 4 rises (0 ms), the lens scales in while its
 * handle extends (80 ms), the right 4 rises (160 ms), the soft circle fades in and the cross
 * draws itself (380 ms), the words and the corner labels rise one after another (600 ms, 60 ms
 * apart), then the lens tilts once. Under reduced motion MotionConfig drops every transform, so
 * only the fades remain, and the cross is simply there.
 */
const DIGIT = { duration: 0.42, ease: EASE }
const LENS_AT = 0.08
const CROSS_AT = 0.38
const WORDS_AT = 0.6
const WORDS_GAP = 0.06
const WIGGLE: Transition = { delay: 0.95, duration: 0.4, ease: EASE }

function Digit({ delay }: { delay: number }) {
  return (
    <motion.span
      initial={{ opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ ...DIGIT, delay }}
      className="inline-block"
    >
      4
    </motion.span>
  )
}

/**
 * The "0" of the 404: a magnifier whose layout box is the ring alone, as wide as a 4 (0.65 em),
 * centred on the digits' cap height. The handle overflows the box, so the gaps either side of
 * the ring match: the margins are the 4's own side bearings subtracted from a 0.05 em gap.
 */
function LensZero() {
  const reduced = useReducedMotion() ?? false
  return (
    <motion.svg
      viewBox="0 0 100 100"
      fill="none"
      strokeLinecap="round"
      aria-hidden="true"
      focusable="false"
      initial={{ opacity: 0, scale: 0.6 }}
      animate={{ opacity: 1, scale: 1, rotate: [0, -9, 6, -2, 0] }}
      transition={{
        opacity: { duration: 0.15, delay: LENS_AT },
        scale: { type: 'spring', stiffness: 220, damping: 26, delay: LENS_AT },
        rotate: WIGGLE,
      }}
      className="mr-[0.015em] ml-[0.021em] inline-block size-[0.65em] overflow-visible align-[0.028em]"
    >
      <circle cx="50" cy="50" r="42.5" stroke="currentColor" strokeWidth="15" />
      <g transform="rotate(45 50 50)">
        <motion.path
          d="M94 50H138"
          stroke="currentColor"
          strokeWidth="15"
          initial={{ scaleX: 0.4 }}
          animate={{ scaleX: 1 }}
          transition={{ type: 'spring', stiffness: 220, damping: 26, delay: LENS_AT }}
          style={{ originX: 0 }}
        />
      </g>
      <motion.circle
        cx="50"
        cy="50"
        r="18"
        className="fill-lift"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.2, delay: CROSS_AT }}
      />
      {['M43 43l14 14', 'M57 43L43 57'].map((d, index) => (
        <motion.path
          key={d}
          d={d}
          strokeWidth="4.5"
          className="stroke-status-listed"
          initial={{ pathLength: reduced ? 1 : 0, opacity: 0 }}
          animate={{ pathLength: 1, opacity: 1 }}
          transition={{
            pathLength: { duration: 0.15, delay: CROSS_AT + index * 0.15, ease: EASE },
            opacity: { duration: 0.01, delay: CROSS_AT + index * 0.15 },
          }}
        />
      ))}
    </motion.svg>
  )
}

/** One of the words or labels rising in at its place in the sequence. */
function Rise({
  order,
  className,
  children,
}: {
  order: number
  className?: string
  children: ReactNode
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ ...settle, delay: WORDS_AT + order * WORDS_GAP }}
      className={className}
    >
      {children}
    </motion.div>
  )
}

export function NotFoundPage() {
  return (
    <>
      <PageBar title="Not found" />
      <PageBody>
        {/* Fills the window inside PageBody's padding, so the corner labels sit at its bottom edge. */}
        <div className="flex min-h-[calc(100dvh-220px-env(safe-area-inset-top)-env(safe-area-inset-bottom))] flex-col lg:min-h-[calc(100dvh-212px)]">
          <div className="flex flex-1 flex-col items-center justify-center pb-10 text-center">
            <p
              aria-hidden="true"
              className="text-[clamp(120px,17vw,248px)] leading-none font-bold whitespace-nowrap text-ink"
            >
              <Digit delay={0} />
              <LensZero />
              <Digit delay={0.16} />
            </p>
            <Rise order={0} className="mt-8 lg:mt-10">
              <h1 className="text-heading">Nothing to see here.</h1>
            </Rise>
            <Rise order={1} className="mt-2">
              <p className="text-body text-ink-muted">
                We couldn’t find the page you’re looking for.
              </p>
            </Rise>
            <Rise order={2} className="mt-8">
              <ButtonLink variant="primary" icon="arrowLeft" to="/lookup" className="h-11 px-6">
                Back to Lookup
              </ButtonLink>
            </Rise>
          </div>
          <Rise order={3} className="flex items-end justify-between gap-4">
            <p className="text-label">Error / 404</p>
            <p className="text-label">Page not found</p>
          </Rise>
        </div>
      </PageBody>
    </>
  )
}
