import { motion } from 'motion/react'
import { OrganizationMark, PersonMark } from '@/components/illustrations'
import type { Provider } from '../../lib/api.ts'

/** The provider's kind as a drawing in a quiet circle; shared between the row and the page header. */
export function ProviderMark({
  provider,
  size = 'md',
}: {
  provider: Pick<Provider, 'id' | 'entity_kind'>
  size?: 'md' | 'lg'
}) {
  const Mark = provider.entity_kind === 'organization' ? OrganizationMark : PersonMark
  const label = provider.entity_kind === 'organization' ? 'Organization' : 'Individual'
  return (
    <motion.span
      layoutId={`provider-mark-${provider.id}`}
      className={`grid shrink-0 place-items-center rounded-full border border-line bg-surface text-ink ${size === 'lg' ? 'size-12 lg:size-16' : 'size-10'}`}
    >
      <Mark size={size === 'lg' ? 26 : 20} title={label} />
    </motion.span>
  )
}
