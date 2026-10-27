import { motion } from 'motion/react'
import { Link, NavLink } from 'react-router'
import { Wordmark } from '@/components/illustrations'
import { glide } from '../../components/motion.ts'
import { BarGlass } from '../../components/PageBar.tsx'
import { DESKTOP, useMedia } from '../../lib/media.ts'
import { useBarGlass } from '../../lib/scroll.ts'
import { NAV } from './nav.ts'

/**
 * The desktop top bar: the wordmark and the sections as one segmented pill with a gliding ink
 * indicator, in a pill 20 px below the window edge and as wide as the content column. It is
 * transparent at the top of the page and its liquid glass fades in with the scroll; the gap above
 * it shows the page passing by. It floats over the page (fixed, out of the flow), so `PageBody`
 * alone sets where a page starts. Phone has `PageBar` and the tab bar instead, and this bar is not
 * mounted there at all: hidden navigation must not keep a layout indicator alive. (A route change
 * made while scrolled relies on ScrollToTop in App.tsx resetting the scroll only after Motion has
 * measured the indicator; see there.)
 */
export function TopBar() {
  const desktop = useMedia(DESKTOP)
  const glass = useBarGlass()
  if (!desktop) return null
  return (
    <header className="pointer-events-none fixed inset-x-0 top-0 z-30 pt-5">
      <div className="pointer-events-auto relative mx-auto grid h-14 w-[calc(100%-96px)] max-w-[1280px] grid-cols-[1fr_auto_1fr] items-center px-5">
        <BarGlass opacity={glass} />
        <Link
          to="/lookup"
          className="relative justify-self-start rounded-full text-body text-ink"
          aria-label="Credentialing, go to lookup"
        >
          <Wordmark label="Credentialing" size={22} />
        </Link>
        <nav aria-label="Main" className="relative">
          <ul className="flex items-center gap-0.5 rounded-full bg-soft p-1">
            {NAV.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  className={({ isActive }) =>
                    `relative flex h-8 items-center rounded-full px-4 text-sm font-medium transition-colors duration-200 ${isActive ? 'text-on-ink' : 'text-ink hover:bg-surface'}`
                  }
                >
                  {({ isActive }) => (
                    <>
                      {isActive && (
                        <motion.span
                          layoutId="nav-active"
                          transition={glide}
                          aria-hidden="true"
                          className="absolute inset-0 rounded-full bg-ink"
                        />
                      )}
                      <span className="relative">{item.label}</span>
                    </>
                  )}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </header>
  )
}
