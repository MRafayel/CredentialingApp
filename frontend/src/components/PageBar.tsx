import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { useScrolledPast } from '../lib/scroll.ts'
import { Icon } from './Icon.tsx'

type Back = { to: string; label: string }

/**
 * The top bars' liquid glass: a floating pill behind the bar's content that fades in once the
 * page scrolls under it. The bar keeps its place and size either way, so nothing moves.
 */
export function BarGlass({ shown }: { shown: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={`glass-bar pointer-events-none absolute inset-0 rounded-full transition-opacity duration-[240ms] ease-out-soft ${shown ? 'opacity-100' : 'opacity-0'}`}
    />
  )
}

/**
 * The phone's top bar: a way back and the page's name, a pill 20 px below the window edge and
 * inside the page gutters. Transparent over the page header; once that has scrolled away the
 * pill turns to glass and the name fades in. Desktop has the shell's top bar instead.
 */
export function PageBar({ title, back }: { title: string; back?: Back }) {
  const scrolled = useScrolledPast(48)
  return (
    <header className="pointer-events-none sticky top-0 z-30 px-5 pt-[calc(env(safe-area-inset-top)+20px)] lg:hidden">
      <div className="pointer-events-auto relative flex h-12 items-center gap-2 px-1">
        <BarGlass shown={scrolled} />
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
        <p
          aria-hidden={!scrolled}
          className={`relative min-w-0 flex-1 truncate text-[17px] font-semibold tracking-tight transition-opacity duration-200 ${scrolled ? 'opacity-100' : 'opacity-0'}`}
        >
          {title}
        </p>
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
 * padding keeps the first element at least 32 px clear of the floating bar.
 */
export function PageBody({ children }: { children: ReactNode }) {
  return (
    <main className="mx-auto w-full max-w-[1376px] px-5 pt-8 pb-[calc(env(safe-area-inset-bottom)+7rem)] lg:px-12 lg:pt-14 lg:pb-24">
      {children}
    </main>
  )
}
