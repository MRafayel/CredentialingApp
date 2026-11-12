import { Link } from 'react-router'
import { Icon } from './Icon.tsx'

/**
 * A quiet "Registry" link to an NPI's registry page, the same on every screen; the NPI itself
 * stays a copy button beside it. It sits above a card's cover link (`relative z-10`). `state` is
 * the router state the registry page reads its Back from.
 */
export function RegistryLink({
  npi,
  className,
  state,
}: {
  npi: string
  className?: string
  state?: unknown
}) {
  return (
    <Link
      to={`/npi/${npi}`}
      state={state}
      aria-label={`Open NPI ${npi} in the registry`}
      className={`relative z-10 inline-flex h-7 shrink-0 items-center gap-1 rounded-full px-2 text-small font-medium text-ink-muted transition-colors hover:bg-soft hover:text-ink ${className ?? ''}`}
    >
      <Icon name="registry" size={15} />
      Registry
    </Link>
  )
}
