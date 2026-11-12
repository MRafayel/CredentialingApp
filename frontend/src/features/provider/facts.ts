/**
 * What the registry and the exclusion lists add to the provider's Facts once the NPI is known
 * and the lists have answered: only what the roster does not already say, each value once
 * however many sources carry it, and every added value marked with its sources ("NPPES",
 * "NY list"). The roster's own rows stay as entered. Nothing here decides a status.
 */
import type {
  MatchedRecord,
  NpiRecord,
  Provider,
  SourceStatus,
  Verification,
} from '../../lib/api.ts'
import { registryName, registryPlace } from '../../lib/registry.ts'

export type RegistryTaxonomy = NpiRecord['taxonomies'][number]
export type Licence = { state: string; number: string }

/** A value a source adds, with the sources that carry it, the first to say it first. */
export type Added<T> = { value: T; sources: string[] }

/** A matched exclusion record with the caption of its list ("NY list"). */
export type ListedRecord = { caption: string; record: MatchedRecord }

/** The registry's caption beside a value it adds. */
export const REGISTRY = 'NPPES'

export type AddedFacts = {
  /** Names that read differently from the roster's, as a source writes them. */
  names: Added<string>[]
  /** Other names of the party (the registry's other names, the lists' aliases). */
  otherNames: Added<string>[]
  /** NPIs the lists publish while the roster has none. */
  npis: Added<string>[]
  licences: Added<Licence>[]
  /** Places as "City, ST": the registry's practice place, the lists' city and state. */
  places: Added<string>[]
  /** The registry's taxonomies the roster lacks (the lists have none). */
  taxonomies: RegistryTaxonomy[]
}

type FactsProvider = Pick<
  Provider,
  'person_name' | 'organization_name' | 'npi' | 'licenses' | 'addresses' | 'taxonomy_codes'
>
type FactsRegistry = Pick<
  NpiRecord,
  | 'person_name'
  | 'organization_name'
  | 'other_names'
  | 'taxonomies'
  | 'practice_city'
  | 'practice_state'
>

/** A state and a licence number as letters and digits only, upper case (as the backend compares). */
const licenceKey = (state: string, number: string) =>
  `${fold(state)}|${number.replace(/[^0-9a-z]/gi, '').toUpperCase()}`

const fold = (value: string | null | undefined) => (value ?? '').trim().toUpperCase()

const placeKey = (city: string | null | undefined, state: string | null | undefined) =>
  `${fold(city)}|${fold(state)}`

/**
 * Values keyed for sameness: a value already known (the roster's) is never added, a value a
 * second source carries gains that source, in the order the sources spoke.
 */
class Merged<T> {
  private readonly entries = new Map<string, Added<T>>()
  private readonly known: ReadonlySet<string>

  constructor(known: ReadonlySet<string>) {
    this.known = known
  }

  add(key: string, value: T, source: string): void {
    if (this.known.has(key)) return
    const entry = this.entries.get(key)
    if (!entry) {
      this.entries.set(key, { value, sources: [source] })
    } else if (!entry.sources.includes(source)) {
      entry.sources.push(source)
    }
  }

  has(key: string): boolean {
    return this.entries.has(key)
  }

  take(key: string): Added<T> | undefined {
    const entry = this.entries.get(key)
    this.entries.delete(key)
    return entry
  }

  list(): Added<T>[] {
    return [...this.entries.values()]
  }
}

/** What one source says about the party, in the roster's terms. */
type Contribution = {
  source: string
  name: string | null
  otherNames: readonly string[]
  npi: string | null
  licences: readonly Licence[]
  place: { key: string; text: string } | null
}

function fromRegistry(record: FactsRegistry): Contribution {
  const text = registryPlace(record)
  return {
    source: REGISTRY,
    name: registryName(record),
    otherNames: record.other_names,
    npi: null,
    licences: primaryFirst(record.taxonomies).flatMap((taxonomy) => {
      const state = taxonomy.license_state?.trim()
      const number = taxonomy.license_number?.trim()
      return state && number ? [{ state, number }] : []
    }),
    place: text ? { key: placeKey(record.practice_city, record.practice_state), text } : null,
  }
}

function fromList({ caption, record }: ListedRecord): Contribution {
  const place = record.place
  const text = place ? [place.city, place.state].filter(Boolean).join(', ') : ''
  const licence = record.license
  return {
    source: caption,
    name: registryName(record),
    otherNames: record.aliases,
    npi: record.npi,
    licences: licence ? [{ state: licence.state, number: licence.number }] : [],
    place: text ? { key: placeKey(place?.city, place?.state), text } : null,
  }
}

/**
 * The facts the registry (first, when the NPI is known) and the matched records of the lists
 * add to the roster's. Sameness: names and other names by their text ignoring case; licences
 * by state and the letters and digits of the number (so a number the lists pad or prefix
 * differently stays a row of its own, marked with its list); places by city and state; NPIs
 * as written. A roster name, licence, address or NPI is never repeated; a name one source
 * writes that another lists as an alias is one row with both sources.
 */
export function addedFacts(
  provider: FactsProvider,
  registry: FactsRegistry | null,
  listed: readonly ListedRecord[],
): AddedFacts {
  const ownName = new Set([fold(registryName(provider))].filter(Boolean))
  const names = new Merged<string>(ownName)
  const otherNames = new Merged<string>(ownName)
  const npis = new Merged<string>(new Set(provider.npi ? [provider.npi] : []))
  const licences = new Merged<Licence>(
    new Set(
      provider.licenses
        .filter((licence) => licence.number)
        .map((licence) => licenceKey(licence.state, licence.number ?? '')),
    ),
  )
  const places = new Merged<string>(
    new Set(provider.addresses.map((address) => placeKey(address.city, address.state))),
  )

  const contributions = [...(registry ? [fromRegistry(registry)] : []), ...listed.map(fromList)]
  for (const said of contributions) {
    if (said.name) names.add(fold(said.name), said.name, said.source)
    for (const other of said.otherNames) {
      const key = fold(other)
      if (names.has(key)) names.add(key, other, said.source)
      else otherNames.add(key, other, said.source)
    }
    if (said.npi) npis.add(said.npi, said.npi, said.source)
    for (const licence of said.licences) {
      licences.add(licenceKey(licence.state, licence.number), licence, said.source)
    }
    if (said.place) places.add(said.place.key, said.place.text, said.source)
  }
  // An alias a source gave before another source wrote it as the party's name joins that name.
  for (const [key] of names.list().map((entry) => [fold(entry.value)] as const)) {
    const alias = otherNames.take(key)
    for (const source of alias?.sources ?? []) names.add(key, alias?.value ?? '', source)
  }

  return {
    names: names.list(),
    otherNames: otherNames.list(),
    npis: npis.list(),
    licences: licences.list(),
    places: places.list(),
    taxonomies: registry ? addedTaxonomies(provider, registry) : [],
  }
}

/**
 * The records whose facts may stand beside the roster's: those a completed check matched as
 * the party itself at exact or high confidence. A potential or ambiguous match, or a related
 * party on a listed party's row, stays in the Results alone, since its facts may be someone
 * else's.
 */
export function listedRecords(
  results: readonly Verification[],
  caption: (sourceId: string) => string,
): ListedRecord[] {
  return results.flatMap((result) => {
    const match = result.match
    if (result.outcome !== 'completed' || !match || match.relation !== 'party') return []
    if (match.level !== 'exact' && match.level !== 'high') return []
    return result.matched_records.map((record) => ({ caption: caption(result.source_id), record }))
  })
}

/**
 * A list's short caption beside a fact it adds: "federal list" for the federal list, "NY list"
 * for a state's, else the list's name.
 */
export function listCaption(
  source: Pick<SourceStatus, 'jurisdictions' | 'display_name'> | undefined,
  sourceId: string,
): string {
  if (!source) return sourceId
  if (source.jurisdictions.includes('US')) return 'federal list'
  const [state, ...rest] = source.jurisdictions
  return state && rest.length === 0 ? `${state} list` : source.display_name
}

/**
 * The registry's taxonomies whose code the roster does not list, primary first, each code,
 * licence state and number once.
 */
export function addedTaxonomies(
  provider: Pick<Provider, 'taxonomy_codes'>,
  record: Pick<NpiRecord, 'taxonomies'>,
): RegistryTaxonomy[] {
  const listed = new Set(provider.taxonomy_codes.map(fold))
  const seen = new Set<string>()
  const added: RegistryTaxonomy[] = []
  for (const taxonomy of primaryFirst(record.taxonomies)) {
    if (listed.has(fold(taxonomy.code))) continue
    const key = [
      fold(taxonomy.code),
      fold(taxonomy.license_state),
      (taxonomy.license_number ?? '').replace(/[^0-9a-z]/gi, '').toUpperCase(),
    ].join('|')
    if (seen.has(key)) continue
    seen.add(key)
    added.push(taxonomy)
  }
  return added
}

/** The registry's taxonomies with the primary one first, the rest in their order. */
function primaryFirst(taxonomies: readonly RegistryTaxonomy[]): RegistryTaxonomy[] {
  return [...taxonomies].sort((a, b) => Number(b.primary) - Number(a.primary))
}
