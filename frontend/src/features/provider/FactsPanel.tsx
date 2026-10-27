import type { ReactNode } from 'react'
import { CopyValue } from '../../components/CopyValue.tsx'
import { CodePill } from '../../components/Status.tsx'
import { DateOf } from '../../components/Time.tsx'
import type { Provider } from '../../lib/api.ts'

function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 py-3">
      <dt className="text-small text-ink-muted">{term}</dt>
      <dd className="min-w-0 text-body break-words">{children}</dd>
    </div>
  )
}

const NONE = <span className="text-ink-muted">None</span>

/** What the roster holds about the provider. The date of birth itself is never sent back. */
export function FactsPanel({ provider }: { provider: Provider }) {
  const name = provider.person_name
  return (
    <section aria-labelledby="facts-heading">
      <h2 id="facts-heading" className="text-subheading">
        Facts
      </h2>
      <dl className="mt-4 divide-y divide-line rounded-card bg-surface px-5 py-1 shadow-card">
        <Fact term="Kind">
          {provider.entity_kind === 'organization' ? 'Organization' : 'Individual'}
        </Fact>
        {name ? (
          <Fact term="Name">
            <span className="font-semibold">{name.last}</span>
            {name.first && `, ${name.first}`}
            {name.middle && ` ${name.middle}`}
            {name.suffix && ` ${name.suffix}`}
          </Fact>
        ) : (
          <Fact term="Organization">{provider.organization_name ?? NONE}</Fact>
        )}
        <Fact term="NPI">
          {provider.npi ? <CopyValue value={provider.npi} label="NPI" className="-ml-1.5" /> : NONE}
        </Fact>
        {provider.entity_kind === 'individual' && (
          <Fact term="Date of birth on file">{provider.has_date_of_birth ? 'Yes' : 'No'}</Fact>
        )}
        <Fact term="Licences">
          {provider.licenses.length ? (
            <ul className="flex flex-col gap-1">
              {provider.licenses.map((licence) => (
                <li key={`${licence.state}-${licence.number}`} className="flex items-center gap-2">
                  <CodePill>{licence.state}</CodePill>
                  <span className="font-mono text-small">{licence.number}</span>
                  {licence.kind && <span className="text-ink-muted">{licence.kind}</span>}
                </li>
              ))}
            </ul>
          ) : (
            NONE
          )}
        </Fact>
        <Fact term="Addresses">
          {provider.addresses.length ? (
            <ul className="flex flex-col gap-1">
              {provider.addresses.map((address, index) => (
                <li key={index}>
                  {[address.line1, address.line2, address.city, address.state, address.postal_code]
                    .filter(Boolean)
                    .join(', ')}
                </li>
              ))}
            </ul>
          ) : (
            NONE
          )}
        </Fact>
        <Fact term="Taxonomy codes">
          {provider.taxonomy_codes.length ? (
            <span className="font-mono text-small">{provider.taxonomy_codes.join(', ')}</span>
          ) : (
            NONE
          )}
        </Fact>
        <Fact term="Added">
          <DateOf at={provider.created_at} />
        </Fact>
      </dl>
    </section>
  )
}
