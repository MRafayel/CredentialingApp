import { motion } from 'motion/react'
import { useId } from 'react'
import { Link } from 'react-router'
import { rise } from '../../components/motion.ts'
import { SourceLine } from '../../components/Provenance.tsx'
import { CodePill } from '../../components/Status.tsx'
import { CalendarDate } from '../../components/Time.tsx'
import type { NpiRecord } from '../../lib/api.ts'
import { entityKindWord } from '../../lib/provider.ts'
import { registryPlace, type FactsSource } from '../../lib/registry.ts'
import { Fact } from '../provider/FactsPanel.tsx'
import { useFrom } from '../shell/back.ts'

/** An NPI as a link to its registry page, leading back to the page around it. */
export function NpiLink({ npi }: { npi: string }) {
  const from = useFrom()
  return (
    <Link
      to={`/npi/${npi}`}
      state={from}
      className="rounded-sm font-mono font-medium text-accent-strong underline-offset-4 hover:underline"
    >
      {npi}
    </Link>
  )
}

/**
 * What NPPES records about the NPI, as registered; empty facts are left out. `source`, when
 * given, says under the heading where the facts come from and as of when (the stored file or
 * the live registry), so a cached record never reads as live.
 */
export function RegistryFacts({
  record,
  source = null,
}: {
  record: NpiRecord
  source?: FactsSource | null
}) {
  const headingId = useId()
  const name = record.person_name
  const place = registryPlace(record)
  const taxonomies = [...record.taxonomies].sort((a, b) => Number(b.primary) - Number(a.primary))
  const official = record.authorized_official
  const dates: { term: string; value: string | null }[] = [
    { term: 'Enumerated', value: record.enumeration_date },
    { term: 'Last updated', value: record.last_updated },
    { term: 'Certified', value: record.certification_date },
    { term: 'Deactivated', value: record.deactivation_date },
    { term: 'Reactivated', value: record.reactivation_date },
  ]
  return (
    <section aria-labelledby={headingId}>
      <h2 id={headingId} className="text-subheading">
        In NPPES
      </h2>
      {source && <SourceLine block={source.block} kind={source.kind} className="mt-1" />}
      <motion.dl
        variants={rise}
        initial="hidden"
        animate="shown"
        className="mt-4 divide-y divide-line rounded-card bg-surface px-5 py-1 shadow-card"
      >
        <Fact term="Kind">{entityKindWord(record.entity_kind)}</Fact>
        {name ? (
          <Fact term="Name">
            {record.name_prefix && `${record.name_prefix} `}
            <span className="font-semibold">{name.last}</span>
            {name.first && `, ${name.first}`}
            {name.middle && ` ${name.middle}`}
            {name.suffix && ` ${name.suffix}`}
          </Fact>
        ) : (
          record.organization_name && <Fact term="Organization">{record.organization_name}</Fact>
        )}
        {record.credential && <Fact term="Credential">{record.credential}</Fact>}
        {record.other_names.length > 0 && (
          <Fact term="Other names">
            <ul className="flex flex-col gap-1">
              {record.other_names.map((other) => (
                <li key={other}>{other}</li>
              ))}
            </ul>
          </Fact>
        )}
        {place && <Fact term="Practice place">{place}</Fact>}
        {taxonomies.length > 0 && (
          <Fact term="Taxonomies">
            <ul className="flex flex-col gap-1.5">
              {taxonomies.map((taxonomy, index) => (
                <li
                  key={`${taxonomy.code}-${index}`}
                  className="flex flex-wrap items-center gap-x-2 gap-y-1"
                >
                  <span className="font-mono text-small">{taxonomy.code}</span>
                  {taxonomy.license_state && <CodePill>{taxonomy.license_state}</CodePill>}
                  {taxonomy.license_number && (
                    <span className="font-mono text-small text-ink-muted">
                      {taxonomy.license_number}
                    </span>
                  )}
                  {taxonomy.primary && (
                    <span className="text-caption font-medium text-ink-muted">Primary</span>
                  )}
                </li>
              ))}
            </ul>
          </Fact>
        )}
        {official && (
          <Fact term="Authorized official">
            {[
              official.name.last,
              [official.name.first, official.name.middle].filter(Boolean).join(' '),
            ]
              .filter(Boolean)
              .join(', ')}
            {official.title && <span className="text-ink-muted"> · {official.title}</span>}
          </Fact>
        )}
        {dates.map(
          (item) =>
            item.value && (
              <Fact key={item.term} term={item.term}>
                <CalendarDate value={item.value} />
              </Fact>
            ),
        )}
        {record.replacement_npi && (
          <Fact term="Replacement NPI">
            {record.replacement_npi === record.npi ? (
              <span className="font-mono">{record.replacement_npi}</span>
            ) : (
              <NpiLink npi={record.replacement_npi} />
            )}
          </Fact>
        )}
        {/* Shown only when a full file dropped the NPI: the usual "yes" is no fact worth a row
            (owner, 2026-11-10). It is about publication, not status: a deactivated NPI the
            file still lists is "in the registry". */}
        {!record.in_file && (
          <Fact term="Currently in NPI registry">
            <span className="font-medium text-ink">No</span>
          </Fact>
        )}
      </motion.dl>
    </section>
  )
}
