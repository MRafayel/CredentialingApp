import { motion, useReducedMotion, type Transition } from 'motion/react'
import { useState, type ComponentProps, type FocusEvent, type ReactNode } from 'react'
import { Link } from 'react-router'
import { Icon, type IconName } from './Icon.tsx'
import { EASE, press, quick } from './motion.ts'

type Variant = 'primary' | 'secondary' | 'ghost'

const BASE =
  'inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-full px-4 text-sm font-medium whitespace-nowrap select-none transition-[background-color,border-color,color,opacity] disabled:cursor-not-allowed disabled:opacity-50'

/** Primary is the one ink pill per view; secondary is outlined; ghost is quiet. */
const VARIANTS: Record<Variant, string> = {
  primary: 'bg-ink px-5 text-on-ink hover:opacity-85',
  secondary: 'border border-line-strong bg-surface text-ink hover:border-ink-faint',
  ghost: 'text-ink-muted hover:bg-soft hover:text-ink',
}

type Common = {
  variant?: Variant
  icon?: IconName
  /**
   * Spin the icon a full turn clockwise (500 ms, `EASE`) each time the pointer enters the button
   * or keyboard focus lands on it. It never spins back: at 360° it is reset to 0° with no
   * animation, ready for the next entry. Off under reduced motion. Meant for "+" actions.
   */
  spinIconOnHover?: boolean
  children: ReactNode
  className?: string
}

function buttonClass(variant: Variant = 'secondary', className?: string) {
  return [BASE, VARIANTS[variant], className].filter(Boolean).join(' ')
}

/** One full turn, then back to 0° with no animation so the next entry spins again. */
const SPIN: Transition = { duration: 0.5, ease: EASE }
const RESET: Transition = { duration: 0 }

/** The icon's spin state and the triggers that start it; a spin under way is not restarted. */
function useIconSpin(enabled: boolean) {
  const [spinning, setSpinning] = useState(false)
  const reduced = useReducedMotion() ?? false
  const spin = () => {
    if (enabled && !reduced && !spinning) setSpinning(true)
  }
  const spinOnFocus = (event: FocusEvent<HTMLElement>) => {
    if (event.currentTarget.matches(':focus-visible')) spin()
  }
  const settle = () => {
    if (spinning) setSpinning(false)
  }
  return { spinning, spin, spinOnFocus, settle }
}

function ButtonIcon({
  name,
  spinning,
  onSpun,
}: {
  name: IconName
  spinning: boolean
  onSpun: () => void
}) {
  return (
    <motion.span
      aria-hidden="true"
      initial={false}
      animate={{ rotate: spinning ? 360 : 0 }}
      transition={spinning ? SPIN : RESET}
      onAnimationComplete={onSpun}
      className="inline-flex"
    >
      <Icon name={name} size={17} />
    </motion.span>
  )
}

export function Button({
  variant,
  icon,
  spinIconOnHover = false,
  children,
  className,
  type = 'button',
  onHoverStart,
  onFocus,
  ...props
}: Common & Omit<ComponentProps<typeof motion.button>, 'children'>) {
  const { spinning, spin, spinOnFocus, settle } = useIconSpin(spinIconOnHover && !props.disabled)
  return (
    <motion.button
      type={type}
      whileTap={props.disabled ? undefined : press}
      transition={quick}
      className={buttonClass(variant, className)}
      onHoverStart={(event, info) => {
        spin()
        onHoverStart?.(event, info)
      }}
      onFocus={(event) => {
        spinOnFocus(event)
        onFocus?.(event)
      }}
      {...props}
    >
      {icon && <ButtonIcon name={icon} spinning={spinning} onSpun={settle} />}
      {children}
    </motion.button>
  )
}

const MotionLink = motion.create(Link)

export function ButtonLink({
  variant,
  icon,
  spinIconOnHover = false,
  children,
  className,
  onHoverStart,
  onFocus,
  ...props
}: Common & Omit<ComponentProps<typeof MotionLink>, 'children'>) {
  const { spinning, spin, spinOnFocus, settle } = useIconSpin(spinIconOnHover)
  return (
    <MotionLink
      whileTap={press}
      transition={quick}
      className={buttonClass(variant, className)}
      onHoverStart={(event, info) => {
        spin()
        onHoverStart?.(event, info)
      }}
      onFocus={(event) => {
        spinOnFocus(event)
        onFocus?.(event)
      }}
      {...props}
    >
      {icon && <ButtonIcon name={icon} spinning={spinning} onSpun={settle} />}
      {children}
    </MotionLink>
  )
}
