import { motion } from 'motion/react'
import { Link } from 'react-router'
import { CopyValue } from '../../components/CopyValue.tsx'
import { Icon } from '../../components/Icon.tsx'
import { rise } from '../../components/motion.ts'
import { CodePill } from '../../components/Status.tsx'
import { DateOf } from '../../components/Time.tsx'
import type { Provider } from '../../lib/api.ts'
import { displayName, providerStates } from '../../lib/provider.ts'
import { ProviderMark } from './ProviderMark.tsx'

const ROW = 'flex items-center gap-3 px-4 py-4 sm:gap-4 sm:px-5'

/**
 * One roster card; the whole card opens the provider. It carries no status: the list does not
 * hold check results, so it shows none rather than a guess.
 */
export function ProviderRow({ provider }: { provider: Provider }) {
  const name = displayName(provider)
  const states = providerStates(provider)
  return (
    <motion.li variants={rise} className="relative">
      <div
        className={`group relative rounded-item bg-surface shadow-card transition-[box-shadow,translate,scale] duration-180 ease-out-soft hover:shadow-card-hover active:scale-[0.99] motion-safe:hover:-translate-y-px ${ROW}`}
      >
        <ProviderMark provider={provider} />
        <div className="min-w-0 flex-1">
          <Link
            to={`/providers/${provider.id}`}
            className="block truncate text-title text-ink outline-none after:absolute after:inset-0 after:rounded-item focus-visible:after:outline-2 focus-visible:after:outline-offset-2 focus-visible:after:outline-accent"
          >
            <motion.span layoutId={`provider-name-${provider.id}`} className="inline-block">
              {name}
            </motion.span>
          </Link>
          <div className="flex min-w-0 items-center gap-2 text-small text-ink-muted">
            {provider.npi ? (
              <CopyValue value={provider.npi} label="NPI" className="relative z-10 -ml-1.5" />
            ) : (
              <span className="h-7 leading-7">No NPI</span>
            )}
          </div>
        </div>
        {states.length > 0 && (
          <span className="flex shrink-0 items-center gap-1">
            <span className="sr-only">States {states.join(', ')}</span>
            <span aria-hidden="true" className="flex items-center gap-1">
              {states.slice(0, 3).map((state) => (
                <CodePill key={state}>{state}</CodePill>
              ))}
              {states.length > 3 && (
                <span className="text-caption font-semibold text-ink-muted">
                  +{states.length - 3}
                </span>
              )}
            </span>
          </span>
        )}
        <DateOf
          at={provider.created_at}
          className="hidden w-28 shrink-0 text-right text-small text-ink-muted tabular-nums md:block"
        />
        <Icon
          name="chevronRight"
          size={16}
          className="hidden shrink-0 text-ink-faint transition-[transform,color] group-hover:translate-x-0.5 group-hover:text-ink sm:block"
        />
      </div>
    </motion.li>
  )
}

export function ProviderRowSkeleton() {
  return (
    <li className={`rounded-item bg-surface shadow-card ${ROW}`}>
      <span className="skeleton size-10 shrink-0 rounded-full" />
      <span className="flex flex-1 flex-col gap-2">
        <span className="skeleton h-4 w-44 max-w-full rounded-md" />
        <span className="skeleton h-3 w-28 rounded-md" />
      </span>
      <span className="skeleton hidden h-6 w-10 rounded-full sm:block" />
      <span className="skeleton hidden h-3 w-24 rounded-md md:block" />
    </li>
  )
}
