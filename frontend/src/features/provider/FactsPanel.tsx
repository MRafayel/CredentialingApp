import { motion } from 'motion/react'
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { CopyValue } from '../../components/CopyValue.tsx'
import { settle } from '../../components/motion.ts'
import { CodePill } from '../../components/Status.tsx'
import { CalendarDate, DateOf } from '../../components/Time.tsx'
import type { NpiRecord, Provider } from '../../lib/api.ts'
import { roleWord } from '../../lib/labels.ts'
import { useFrom } from '../shell/back.ts'
import { REGISTRY, addedFacts, type ListedRecord, type RegistryTaxonomy } from './facts.ts'
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

/**
 * The quiet mark after a value that is not the roster's: the sources that say it ("NPPES",
 * "NPPES · NY list"), the same in every row.
 */
function Mark({
  sources = [REGISTRY],
  prefix = '',
  className = 'ml-2',
}: {
  sources?: readonly string[]
  prefix?: string
  className?: string
}) {
  return (
    <span className={`text-caption whitespace-nowrap text-ink-muted ${className}`}>
      {prefix}
      {sources.join(' · ')}
    </span>
  )
}

const NONE = <span className="text-ink-muted">None</span>

/**
 * Everything known about the provider: the roster's facts and, in the same rows, what NPPES
 * records about the NPI once it is known and what the lists that matched the provider as the
 * party itself say about them (their licence, place, other names, published NPI; what belongs
 * to the listing itself stays in the Results). Each added value is marked with its sources,
 * and a value two sources carry is one row with both marks; a value the roster already holds is
 * never repeated. Without a registry record or a listed record it is the roster's facts alone.
 * The date of birth itself is never sent back.
 */
export function FactsPanel({
  provider,
  registry = null,
  listed = [],
}: {
  provider: Provider
  registry?: NpiRecord | null
  listed?: readonly ListedRecord[]
}) {
  const name = provider.person_name
  const added = addedFacts(provider, registry, listed)
  return (
    // On desktop the heading and the card are two rows of the page's grid, so the card's top
    // lines up with the NPI card's across the page (owner, 2026-11-12).
    <section aria-labelledby="facts-heading" className="lg:row-span-2 lg:grid lg:grid-rows-subgrid">
      <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        <h2 id="facts-heading" className="text-subheading">
          Facts
        </h2>
      </div>
      {/* `self-start`: the card ends with its last row, never stretched to the left column. */}
      <dl className="mt-4 divide-y divide-line rounded-card bg-surface px-5 py-1 shadow-card lg:mt-0 lg:self-start">
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
          {added.names.map((item) => (
            <motion.span {...ARRIVE} key={item.value} className="block">
              {item.value}
              <Mark sources={item.sources} prefix="in " />
            </motion.span>
          ))}
        </Fact>
        {added.otherNames.length > 0 && (
          <Fact term="Other names" added>
            <ul className="flex flex-col gap-1">
              {added.otherNames.map((item) => (
                <li key={item.value}>
                  {item.value}
                  <Mark sources={item.sources} />
                </li>
              ))}
            </ul>
          </Fact>
        )}
        {registry?.credential && (
          <Fact term="Credential" added>
            {registry.credential}
            <Mark />
          </Fact>
        )}
        <Fact term="NPI">
          {provider.npi ? (
            <span className="flex flex-wrap items-center gap-x-1">
              <CopyValue value={provider.npi} label="NPI" className="-ml-1.5" />
              <RegistryLink npi={provider.npi} />
            </span>
          ) : added.npis.length > 0 ? (
            <ul className="flex flex-col gap-1">
              {added.npis.map((item) => (
                <motion.li
                  {...ARRIVE}
                  key={item.value}
                  className="flex flex-wrap items-center gap-x-1"
                >
                  <CopyValue value={item.value} label="NPI" className="-ml-1.5" />
                  <RegistryLink npi={item.value} />
                  <Mark sources={item.sources} prefix="published by " className="" />
                </motion.li>
              ))}
            </ul>
          ) : (
            NONE
          )}
        </Fact>
        {provider.entity_kind === 'individual' && (
          <Fact term="Date of birth on file">{provider.has_date_of_birth ? 'Yes' : 'No'}</Fact>
        )}
        <Fact term="Licences">
          {provider.licenses.length || added.licences.length ? (
            <ul className="flex flex-col gap-1">
              {provider.licenses.map((licence) => (
                <li
                  key={`${licence.state}-${licence.number ?? ''}`}
                  className="flex items-center gap-2"
                >
                  <CodePill>{licence.state}</CodePill>
                  {licence.number ? (
                    <span className="font-mono text-small">{licence.number}</span>
                  ) : (
                    <span className="text-small text-ink-muted">state only</span>
                  )}
                  {licence.kind && <span className="text-ink-muted">{licence.kind}</span>}
                </li>
              ))}
              {added.licences.map((item) => (
                <motion.li
                  {...ARRIVE}
                  key={`added-${item.value.state}-${item.value.number}`}
                  className="flex items-center gap-2"
                >
                  <CodePill>{item.value.state}</CodePill>
                  <span className="font-mono text-small">{item.value.number}</span>
                  <Mark sources={item.sources} className="" />
                </motion.li>
              ))}
            </ul>
          ) : (
            NONE
          )}
        </Fact>
        <Fact term="Addresses">
          {provider.addresses.length || added.places.length ? (
            <ul className="flex flex-col gap-1">
              {provider.addresses.map((address, index) => (
                <li key={index}>
                  {[address.line1, address.line2, address.city, address.state, address.postal_code]
                    .filter(Boolean)
                    .join(', ')}
                </li>
              ))}
              {added.places.map((item) => (
                <motion.li {...ARRIVE} key={item.value}>
                  {item.value}
                  <Mark sources={item.sources} />
                </motion.li>
              ))}
            </ul>
          ) : (
            NONE
          )}
        </Fact>
        <Fact term="Taxonomies">
          {provider.taxonomy_codes.length || added.taxonomies.length ? (
            <ul className="flex flex-col gap-1.5">
              {provider.taxonomy_codes.length > 0 && (
                <li className="font-mono text-small">{provider.taxonomy_codes.join(', ')}</li>
              )}
              {added.taxonomies.map((taxonomy, index) => (
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
        {registry && <RegistryRows record={registry} />}
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
      <Mark className="" />
    </>
  )
}

/**
 * The rows only the registry has: the authorized official, its dates, the replacement NPI and,
 * only when a full file dropped the NPI, "Currently in NPI registry · No" (never a "Yes" row).
 */
function RegistryRows({ record }: { record: NpiRecord }) {
  const from = useFrom()
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
          <Mark />
        </Fact>
      )}
      {dates.map(
        (item) =>
          item.value && (
            <Fact key={item.term} term={item.term} added>
              <CalendarDate value={item.value} />
              <Mark />
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
              state={from}
              className="rounded-sm font-mono font-medium text-accent-strong underline-offset-4 hover:underline"
            >
              {record.replacement_npi}
            </Link>
          )}
          <Mark />
        </Fact>
      )}
      {!record.in_file && (
        <Fact term="Currently in NPI registry" added>
          <span className="font-medium text-ink">No</span>
          <Mark />
        </Fact>
      )}
    </>
  )
}
