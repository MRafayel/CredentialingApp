import { motion } from 'motion/react'
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { CopyValue } from '../../components/CopyValue.tsx'
import { settle } from '../../components/motion.ts'
import { SourceLine } from '../../components/Provenance.tsx'
import { CodePill } from '../../components/Status.tsx'
import { CalendarDate, DateOf } from '../../components/Time.tsx'
import type { LiveCheck, MirrorSource, NpiRecord, Provider } from '../../lib/api.ts'
import { roleWord } from '../../lib/labels.ts'
import {
  addedLicences,
  addedPlace,
  addedTaxonomies,
  registryNameIfDifferent,
  type RegistryTaxonomy,
} from './facts.ts'
import { RegistryLink } from './RegistryLink.tsx'

/** A registry row or value arriving after the roster's rows: it rises in. */
const ARRIVE = { initial: { opacity: 0, y: 6 }, animate: { opacity: 1, y: 0 }, transition: settle }

/**
 * One term and its value in a facts panel. `added` marks a row that arrives later (the
 * registry's), which rises in.
 */
export function Fact({
  term,
  children,
  added = false,
}: {
  term: string
  children: ReactNode
  added?: boolean
}) {
  const body = (
    <>
      <dt className="text-small text-ink-muted">{term}</dt>
      <dd className="min-w-0 text-body break-words">{children}</dd>
    </>
  )
  return added ? (
    <motion.div {...ARRIVE} className="flex flex-col gap-0.5 py-3">
      {body}
    </motion.div>
  ) : (
    <div className="flex flex-col gap-0.5 py-3">{body}</div>
  )
}

/** The quiet mark after a value that comes from the registry, the same in every row. */
function Nppes({ words = 'NPPES', className = 'ml-2' }: { words?: string; className?: string }) {
  return (
    <span className={`text-caption whitespace-nowrap text-ink-muted ${className}`}>{words}</span>
  )
}

const NONE = <span className="text-ink-muted">None</span>

/** What NPPES records about the provider's NPI, with where it was read from. */
export type RegistryFactsInput = {
  record: NpiRecord
  source: MirrorSource | LiveCheck
  kind: 'mirror' | 'live'
}

/**
 * Everything known about the provider: the roster's facts and, once the NPI's registry record
 * has arrived, what NPPES adds in the same rows (each such value marked "NPPES", the registry's
 * source and date once under the heading). Without the registry it is the roster's facts alone.
 * The date of birth itself is never sent back.
 */
export function FactsPanel({
  provider,
  registry = null,
}: {
  provider: Provider
  registry?: RegistryFactsInput | null
}) {
  const name = provider.person_name
  const record = registry?.record ?? null
  const theirName = record ? registryNameIfDifferent(provider, record) : null
  const licences = record ? addedLicences(provider, record) : []
  const place = record ? addedPlace(provider, record) : null
  const taxonomies = record ? addedTaxonomies(provider, record) : []
  return (
    <section aria-labelledby="facts-heading">
      <h2 id="facts-heading" className="text-subheading">
        Facts
      </h2>
      {registry && (
        <motion.p {...ARRIVE} className="mt-1">
          <SourceLine block={registry.source} kind={registry.kind} lead="Includes NPPES" />
        </motion.p>
      )}
      <dl className="mt-4 divide-y divide-line rounded-card bg-surface px-5 py-1 shadow-card">
        <Fact term="Kind">
          {provider.entity_kind === 'organization' ? 'Organization' : 'Individual'}
        </Fact>
        <Fact term="Role">{roleWord(provider.role)}</Fact>
        <Fact term={name ? 'Name' : 'Organization'}>
          {name ? (
            <span className="block">
              <span className="font-semibold">{name.last}</span>
              {name.first && `, ${name.first}`}
              {name.middle && ` ${name.middle}`}
              {name.suffix && ` ${name.suffix}`}
            </span>
          ) : (
            <span className="block">{provider.organization_name ?? NONE}</span>
          )}
          {theirName && (
            <motion.span {...ARRIVE} className="block">
              {theirName}
              <Nppes words="in NPPES" />
            </motion.span>
          )}
        </Fact>
        {record && record.other_names.length > 0 && (
          <Fact term="Other names" added>
            <ul className="flex flex-col gap-1">
              {record.other_names.map((other) => (
                <li key={other}>
                  {other}
                  <Nppes />
                </li>
              ))}
            </ul>
          </Fact>
        )}
        {record?.credential && (
          <Fact term="Credential" added>
            {record.credential}
            <Nppes />
          </Fact>
        )}
        <Fact term="NPI">
          {provider.npi ? (
            <span className="flex flex-wrap items-center gap-x-1">
              <CopyValue value={provider.npi} label="NPI" className="-ml-1.5" />
              <RegistryLink npi={provider.npi} />
            </span>
          ) : (
            NONE
          )}
        </Fact>
        {provider.entity_kind === 'individual' && (
          <Fact term="Date of birth on file">{provider.has_date_of_birth ? 'Yes' : 'No'}</Fact>
        )}
        <Fact term="Licences">
          {provider.licenses.length || licences.length ? (
            <ul className="flex flex-col gap-1">
              {provider.licenses.map((licence) => (
                <li key={`${licence.state}-${licence.number}`} className="flex items-center gap-2">
                  <CodePill>{licence.state}</CodePill>
                  <span className="font-mono text-small">{licence.number}</span>
                  {licence.kind && <span className="text-ink-muted">{licence.kind}</span>}
                </li>
              ))}
              {licences.map((licence) => (
                <motion.li
                  {...ARRIVE}
                  key={`nppes-${licence.state}-${licence.number}`}
                  className="flex items-center gap-2"
                >
                  <CodePill>{licence.state}</CodePill>
                  <span className="font-mono text-small">{licence.number}</span>
                  <Nppes className="" />
                </motion.li>
              ))}
            </ul>
          ) : (
            NONE
          )}
        </Fact>
        <Fact term="Addresses">
          {provider.addresses.length || place ? (
            <ul className="flex flex-col gap-1">
              {provider.addresses.map((address, index) => (
                <li key={index}>
                  {[address.line1, address.line2, address.city, address.state, address.postal_code]
                    .filter(Boolean)
                    .join(', ')}
                </li>
              ))}
              {place && (
                <motion.li {...ARRIVE}>
                  <span className="text-ink-muted">Practice place </span>
                  {place}
                  <Nppes />
                </motion.li>
              )}
            </ul>
          ) : (
            NONE
          )}
        </Fact>
        <Fact term="Taxonomies">
          {provider.taxonomy_codes.length || taxonomies.length ? (
            <ul className="flex flex-col gap-1.5">
              {provider.taxonomy_codes.length > 0 && (
                <li className="font-mono text-small">{provider.taxonomy_codes.join(', ')}</li>
              )}
              {taxonomies.map((taxonomy, index) => (
                <motion.li
                  {...ARRIVE}
                  key={`nppes-${taxonomy.code}-${index}`}
                  className="flex flex-wrap items-center gap-x-2 gap-y-1"
                >
                  <TaxonomyValue taxonomy={taxonomy} />
                </motion.li>
              ))}
            </ul>
          ) : (
            NONE
          )}
        </Fact>
        {record && <RegistryRows record={record} />}
        <Fact term="Added">
          <DateOf at={provider.created_at} />
        </Fact>
      </dl>
    </section>
  )
}

/** A registry taxonomy as `RegistryFacts` shows it: code, licence state and number, Primary. */
function TaxonomyValue({ taxonomy }: { taxonomy: RegistryTaxonomy }) {
  return (
    <>
      <span className="font-mono text-small">{taxonomy.code}</span>
      {taxonomy.license_state && <CodePill>{taxonomy.license_state}</CodePill>}
      {taxonomy.license_number && (
        <span className="font-mono text-small text-ink-muted">{taxonomy.license_number}</span>
      )}
      {taxonomy.primary && <span className="text-caption font-medium text-ink-muted">Primary</span>}
      <Nppes className="" />
    </>
  )
}

/**
 * The rows only the registry has: the authorized official, its dates, the replacement NPI and,
 * only when a full file dropped the NPI, "Currently in NPI registry · No" (never a "Yes" row).
 */
function RegistryRows({ record }: { record: NpiRecord }) {
  const official = record.authorized_official
  const dates: { term: string; value: string | null }[] = [
    { term: 'Enumerated', value: record.enumeration_date },
    { term: 'Last updated', value: record.last_updated },
    { term: 'Certified', value: record.certification_date },
    { term: 'Deactivated', value: record.deactivation_date },
    { term: 'Reactivated', value: record.reactivation_date },
  ]
  return (
    <>
      {official && (
        <Fact term="Authorized official" added>
          {[
            official.name.last,
            [official.name.first, official.name.middle].filter(Boolean).join(' '),
          ]
            .filter(Boolean)
            .join(', ')}
          {official.title && <span className="text-ink-muted"> · {official.title}</span>}
          <Nppes />
        </Fact>
      )}
      {dates.map(
        (item) =>
          item.value && (
            <Fact key={item.term} term={item.term} added>
              <CalendarDate value={item.value} />
              <Nppes />
            </Fact>
          ),
      )}
      {record.replacement_npi && (
        <Fact term="Replacement NPI" added>
          {record.replacement_npi === record.npi ? (
            <span className="font-mono">{record.replacement_npi}</span>
          ) : (
            <Link
              to={`/npi/${record.replacement_npi}`}
              className="rounded-sm font-mono font-medium text-accent-strong underline-offset-4 hover:underline"
            >
              {record.replacement_npi}
            </Link>
          )}
          <Nppes />
        </Fact>
      )}
      {!record.in_file && (
        <Fact term="Currently in NPI registry" added>
          <span className="font-medium text-ink">No</span>
          <Nppes />
        </Fact>
      )}
    </>
  )
}
