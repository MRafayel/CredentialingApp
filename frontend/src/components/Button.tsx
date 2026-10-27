import { motion } from 'motion/react'
import type { ComponentProps, ReactNode } from 'react'
import { Link } from 'react-router'
import { Icon, type IconName } from './Icon.tsx'
import { press, quick } from './motion.ts'

type Variant = 'primary' | 'secondary' | 'ghost'

const BASE =
  'inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-full px-4 text-sm font-medium whitespace-nowrap select-none transition-[background-color,border-color,color,opacity] disabled:cursor-not-allowed disabled:opacity-50'

/** Primary is the one ink pill per view; secondary is outlined; ghost is quiet. */
const VARIANTS: Record<Variant, string> = {
  primary: 'bg-ink px-5 text-on-ink hover:opacity-85',
  secondary: 'border border-line-strong bg-surface text-ink hover:border-ink-faint',
  ghost: 'text-ink-muted hover:bg-soft hover:text-ink',
}

type Common = { variant?: Variant; icon?: IconName; children: ReactNode; className?: string }

function buttonClass(variant: Variant = 'secondary', className?: string) {
  return [BASE, VARIANTS[variant], className].filter(Boolean).join(' ')
}

export function Button({
  variant,
  icon,
  children,
  className,
  type = 'button',
  ...props
}: Common & Omit<ComponentProps<typeof motion.button>, 'children'>) {
  return (
    <motion.button
      type={type}
      whileTap={props.disabled ? undefined : press}
      transition={quick}
      className={buttonClass(variant, className)}
      {...props}
    >
      {icon && <Icon name={icon} size={17} />}
      {children}
    </motion.button>
  )
}

const MotionLink = motion.create(Link)

export function ButtonLink({
  variant,
  icon,
  children,
  className,
  ...props
}: Common & Omit<ComponentProps<typeof MotionLink>, 'children'>) {
  return (
    <MotionLink
      whileTap={press}
      transition={quick}
      className={buttonClass(variant, className)}
      {...props}
    >
      {icon && <Icon name={icon} size={17} />}
      {children}
    </MotionLink>
  )
}
