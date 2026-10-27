import { motion } from 'motion/react'
import { Link, useLocation } from 'react-router'
import { Icon } from '../../components/Icon.tsx'
import { glide, press, quick } from '../../components/motion.ts'
import { DESKTOP, useMedia } from '../../lib/media.ts'
import { NAV } from './nav.ts'

const MotionLink = motion.create(Link)

/**
 * The phone's floating glass tab bar, monochrome: ink icons, the active tab widens and its
 * lens glides over, with blue only on the active label. Add is the one ink pill. Not mounted on
 * desktop (hidden navigation is unmounted, not display: none).
 */
export function TabBar() {
  const { pathname } = useLocation()
  const desktop = useMedia(DESKTOP)
  if (desktop) return null
  return (
    <nav
      aria-label="Main"
      className="fixed inset-x-4 bottom-[calc(env(safe-area-inset-bottom)+12px)] z-40"
    >
      <ul className="mx-auto flex h-16 max-w-sm items-stretch gap-1 rounded-full bg-glass p-1.5 shadow-float backdrop-blur-[20px] backdrop-saturate-150">
        {NAV.map((item) => {
          const active = pathname === item.to || pathname.startsWith(`${item.to}/`)
          return (
            <motion.li
              key={item.to}
              layout
              transition={glide}
              style={{ flexGrow: active ? 1.6 : 1 }}
              className="relative flex basis-0"
            >
              {active && (
                <motion.span
                  layoutId="tab-lens"
                  transition={glide}
                  aria-hidden="true"
                  className="absolute inset-0 rounded-full bg-accent-soft"
                />
              )}
              <MotionLink
                to={item.to}
                aria-current={active ? 'page' : undefined}
                whileTap={press}
                transition={quick}
                className={`relative flex w-full flex-col items-center justify-center gap-0.5 rounded-full text-[11px] font-semibold ${active ? 'text-accent-strong' : 'text-ink'}`}
              >
                <Icon name={item.icon} size={20} />
                {item.label}
              </MotionLink>
            </motion.li>
          )
        })}
        <li className="flex basis-0 grow-[0.9]">
          <MotionLink
            to="/providers/new"
            whileTap={press}
            transition={quick}
            className="flex w-full flex-col items-center justify-center gap-0.5 rounded-full bg-ink text-[11px] font-semibold text-on-ink"
          >
            <Icon name="plus" size={20} />
            Add
          </MotionLink>
        </li>
      </ul>
    </nav>
  )
}
