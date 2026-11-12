import { motion } from 'motion/react'
import { Link } from 'react-router'
import { CopyValue } from '../../components/CopyValue.tsx'
import { Icon } from '../../components/Icon.tsx'
import { rise } from '../../components/motion.ts'
import { CodePill, StatusLabel } from '../../components/Status.tsx'
import { Ago } from '../../components/Time.tsx'
import type { ProviderListItem } from '../../lib/api.ts'
import { attentionLabel, type Label } from '../../lib/labels.ts'
import { displayName, providerStates } from '../../lib/provider.ts'
import { ProviderMark } from './ProviderMark.tsx'

/** A roster gap is never quiet: the review mark and the words, at a glance down the list. */
const NO_NPI: Label = { tone: 'review', word: 'No NPI' }

const ROW = 'flex items-center gap-3 px-4 py-4 sm:gap-4 sm:px-5'

/**
 * One roster card; the whole card opens the provider. Under the name, the backend's one reason
 * for attention as a mark and a word, and where or what in muted text ("Listed · OIG LEIE"); a
 * provider that needs nobody reads "No records" with when it was checked.
 */
export function ProviderRow({
  provider,
  sourceName,
}: {
  provider: ProviderListItem
  sourceName: (id: string) => string
}) {
  const name = displayName(provider)
  const states = providerStates(provider)
  const { status } = provider
  const label = attentionLabel(status, sourceName)
  // The NPI gap shows in its own column only when the line names a more serious reason.
  const npiGap = status.identity === 'npi_missing' && status.attention !== 'npi_missing'
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
          <p className="mt-1 flex min-w-0 items-center gap-1.5 text-small text-ink-muted">
            <StatusLabel label={label} chip size={14} />
            {label.detail && (
              <span className="min-w-0 truncate">
                <span aria-hidden="true">· </span>
                {label.detail}
              </span>
            )}
            {status.attention === null && status.last_checked_at && (
              <span className="min-w-0 truncate md:hidden">
                <span aria-hidden="true">· </span>
                checked <Ago at={status.last_checked_at} />
              </span>
            )}
          </p>
        </div>
        <span className="hidden w-36 shrink-0 items-center text-small text-ink-muted md:flex">
          {provider.npi ? (
            <CopyValue value={provider.npi} label="NPI" className="relative z-10 -ml-1.5" />
          ) : npiGap ? (
            <StatusLabel label={NO_NPI} size={13} className="h-7 text-small" />
          ) : null}
        </span>
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
        <span className="hidden w-28 shrink-0 text-right text-small text-ink-muted tabular-nums md:block">
          {status.last_checked_at ? (
            <>
              <span className="sr-only">Checked </span>
              <Ago at={status.last_checked_at} />
            </>
          ) : (
            'Never checked'
          )}
        </span>
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
        <span className="skeleton h-3 w-32 rounded-md" />
      </span>
      <span className="skeleton hidden h-3 w-24 rounded-md md:block" />
      <span className="skeleton hidden h-6 w-10 rounded-full sm:block" />
      <span className="skeleton hidden h-3 w-16 rounded-md md:block" />
    </li>
  )
}
