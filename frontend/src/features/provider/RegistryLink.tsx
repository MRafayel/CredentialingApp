import { Link, useParams } from 'react-router'
import { Icon } from '../../components/Icon.tsx'

/**
 * The provider page's "Registry" link: it looks like the shared `RegistryLink` but opens the
 * registry over this provider's page (`/providers/:id/registry`) instead of leaving it, so
 * closing the dialog brings the person back where they were.
 */
export function RegistryLink({ npi, className }: { npi: string; className?: string }) {
  const { id = '' } = useParams()
  return (
    <Link
      to={`/providers/${id}/registry`}
      aria-label={`Open NPI ${npi} in the registry`}
      className={`relative z-10 inline-flex h-7 shrink-0 items-center gap-1 rounded-full px-2 text-small font-medium text-ink-muted transition-colors hover:bg-soft hover:text-ink ${className ?? ''}`}
    >
      <Icon name="registry" size={15} />
      Registry
    </Link>
  )
}
