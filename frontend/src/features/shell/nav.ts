import type { IconName } from '../../components/Icon.tsx'

export const NAV: readonly { to: string; label: string; icon: IconName }[] = [
  { to: '/lookup', label: 'Lookup', icon: 'search' },
  { to: '/providers', label: 'Providers', icon: 'providers' },
  { to: '/sources', label: 'Sources', icon: 'sources' },
]
