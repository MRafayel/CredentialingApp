import { motion, type MotionValue } from 'motion/react'
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { DESKTOP, useMedia } from '../lib/media.ts'
import { useBarGlass } from '../lib/scroll.ts'
import { Icon } from './Icon.tsx'

type Back = { to: string; label: string }

/**
 * The top bars' liquid glass: a floating pill behind the bar's content whose opacity follows the
 * scroll (`useBarGlass`), so it fades in and out in proportion, never at a threshold. The blur
 * stays constant; only the layer's opacity changes. The bar keeps its place and size either way.
 */
export function BarGlass({ opacity }: { opacity: MotionValue<number> }) {
  return (
    <motion.span
      aria-hidden="true"
      style={{ opacity }}
      className="glass-bar pointer-events-none absolute inset-0 rounded-full"
    />
  )
}

/**
 * The phone's top bar: a way back and the page's name, a pill 20 px below the window edge and
 * inside the page gutters. Transparent over the page header; as the page scrolls the pill turns
 * to glass and the name fades in with it. It takes no room in the flow (its negative bottom margin
 * equals its height), so `PageBody` alone sets where a page starts. Not mounted on desktop, which
 * has the shell's top bar instead.
 */
export function PageBar({ title, back }: { title: string; back?: Back }) {
  const desktop = useMedia(DESKTOP)
  const glass = useBarGlass()
  if (desktop) return null
  return (
    <header className="pointer-events-none sticky top-0 z-30 -mb-[calc(env(safe-area-inset-top)+68px)] px-5 pt-[calc(env(safe-area-inset-top)+20px)]">
      <div className="pointer-events-auto relative flex h-12 items-center gap-2 px-1">
        <BarGlass opacity={glass} />
        {back ? (
          <Link
            to={back.to}
            aria-label={`Back to ${back.label}`}
            className="relative grid size-10 shrink-0 place-items-center rounded-full text-ink transition-colors hover:bg-soft"
          >
            <Icon name="arrowLeft" />
          </Link>
        ) : (
          <span className="w-2" />
        )}
        {/* The page's own h1 names the page for assistive technology; this is its visual echo. */}
        <motion.p
          aria-hidden="true"
          style={{ opacity: glass }}
          className="relative min-w-0 flex-1 truncate text-[17px] font-semibold tracking-tight"
        >
          {title}
        </motion.p>
      </div>
    </header>
  )
}

/**
 * The top of every page: the title in display type, one muted line under it and the page's
 * primary action on the right. On desktop a back link sits above the title. `centered` makes
 * it a hero (the lookup page): title and line centred, no action.
 */
export function PageHeader({
  title,
  subtitle,
  back,
  action,
  lead,
  centered = false,
}: {
  title: ReactNode
  subtitle?: ReactNode
  back?: Back
  action?: ReactNode
  /** Something drawn before the title, such as the provider's kind mark. */
  lead?: ReactNode
  centered?: boolean
}) {
  if (centered) {
    return (
      <header className="mx-auto flex max-w-2xl flex-col items-center text-center">
        <h1 className="text-heading break-words lg:text-display">{title}</h1>
        {subtitle && <div className="mt-1.5 text-body text-ink-muted lg:mt-2">{subtitle}</div>}
      </header>
    )
  }
  return (
    <header className="flex flex-col gap-3 lg:gap-4">
      {back && (
        <Link
          to={back.to}
          className="-ml-1 hidden items-center gap-1 self-start rounded-full px-1 text-small font-medium text-ink-muted transition-colors hover:text-ink lg:inline-flex"
        >
          <Icon name="arrowLeft" size={15} />
          {back.label}
        </Link>
      )}
      <div className="flex flex-wrap items-end gap-x-6 gap-y-4">
        <div className="flex min-w-0 flex-1 items-center gap-4">
          {lead}
          <div className="min-w-0 flex-1">
            <h1 className="text-heading break-words lg:text-display">{title}</h1>
            {subtitle && <div className="mt-1.5 text-body text-ink-muted lg:mt-2">{subtitle}</div>}
          </div>
        </div>
        {action}
      </div>
    </header>
  )
}

/**
 * The page body: the 1280 px content column, 48 px gutters on desktop and 20 px on phone. Its top
 * padding is the one place a page's start is set: the floating bar's top (20 px) plus its height
 * (48 px phone, 56 px desktop) plus 40 px, so every page's first element sits exactly 40 px below
 * the bar. Pages add no top spacing of their own.
 */
export function PageBody({ children }: { children: ReactNode }) {
  return (
    <main className="mx-auto w-full max-w-[1376px] px-5 pt-[calc(env(safe-area-inset-top)+108px)] pb-[calc(env(safe-area-inset-bottom)+7rem)] lg:px-12 lg:pt-[116px] lg:pb-24">
      {children}
    </main>
  )
}
