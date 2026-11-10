import type { LiveCheck, MirrorSource } from '../lib/api.ts'
import { freshnessLabel } from '../lib/labels.ts'
import { asOf, registrySourceName } from '../lib/registry.ts'
import { Freshness } from './Status.tsx'
import { Ago, DateOf } from './Time.tsx'

/**
 * Where a registry answer comes from, in one quiet line: its freshness with the coloured clock,
 * the source's name, and when it is as of. A mirror block reads "Cached · NPPES monthly file ·
 * as of Nov 2, 2026"; a live block "Live · NPPES live registry · 2 min ago". A status is never
 * shown without one of these beside it.
 */
export function SourceLine({
  block,
  kind,
  className,
}: {
  block: MirrorSource | LiveCheck
  kind: 'mirror' | 'live'
  className?: string
}) {
  return (
    <span
      className={`inline-flex flex-wrap items-center gap-x-1.5 text-small text-ink-muted ${className ?? ''}`}
    >
      <Freshness label={freshnessLabel(block.freshness)} />
      <span aria-hidden="true">·</span>
      <span>{registrySourceName(block.provenance.source_id)}</span>
      <span aria-hidden="true">·</span>
      {kind === 'mirror' ? (
        <span>
          as of <DateOf at={asOf(block)} />
        </span>
      ) : (
        <Ago at={block.provenance.verified_at} />
      )}
    </span>
  )
}
