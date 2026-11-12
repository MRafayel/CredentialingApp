import { describe, expect, it } from 'vitest'
import type { MatchedRecord, NpiRecord, Verification } from '../../lib/api.ts'
import { addedFacts, addedTaxonomies, listCaption, listedRecords } from './facts.ts'

type Taxonomy = NpiRecord['taxonomies'][number]

const taxonomy = (overrides: Partial<Taxonomy>): Taxonomy => ({
  code: '207Q00000X',
  primary: false,
  license_number: null,
  license_state: null,
  group: null,
  ...overrides,
})

const person = (last: string, first: string, middle: string | null = null) => ({
  person_name: { last, first, middle, suffix: null },
  organization_name: null,
})

const address = (city: string | null, state: string | null) => ({
  line1: '1 Example Way',
  line2: null,
  city,
  state,
  postal_code: null,
})

const roster = {
  ...person('Sampleperson', 'Blair'),
  npi: null,
  licenses: [{ state: 'NY', number: '012-345', kind: null }],
  addresses: [address('Sampleville', 'NY')],
  taxonomy_codes: [],
}

const registry = {
  ...person('SAMPLEPERSON', 'BLAIR'),
  other_names: ['SAMPLEFORD, BLAIR'],
  taxonomies: [
    taxonomy({ license_state: 'ny', license_number: '012345' }),
    taxonomy({ license_state: 'NJ', license_number: 'ab 77', primary: true }),
  ],
  practice_city: 'SAMPLEVILLE',
  practice_state: 'NY',
}

const record = (overrides: Partial<MatchedRecord>): MatchedRecord => ({
  id: '00000000-0000-0000-0000-000000000001',
  source_id: 'ny_omig',
  entity_kind: 'individual',
  ...person('Sampleperson', 'Blair'),
  aliases: [],
  related_parties: [],
  npi: null,
  related_npis: [],
  license: null,
  place: null,
  action: 'exclusion',
  action_raw: null,
  effective_date: null,
  end_date: null,
  ...overrides,
})

describe('addedFacts', () => {
  it('adds only what the roster lacks, each value once, marked with every source that says it', () => {
    const listed = [
      {
        caption: 'NY list',
        record: record({
          ...person('Sampleperson', 'Blair', 'Q'),
          aliases: ['Sampleford, Blair'],
          npi: '1234567893',
          license: { state: 'NY', number: '00012345', kind: null },
          place: { city: 'Albany', state: 'NY' },
        }),
      },
      {
        caption: 'federal list',
        record: record({
          aliases: ['SAMPLEPERSON, BLAIR Q'],
          npi: '1234567893',
          license: { state: 'NJ', number: 'AB-77', kind: 'Nurse' },
          place: { city: 'Sampleville', state: 'ny' },
        }),
      },
    ]

    const facts = addedFacts(roster, registry, listed)

    // The registry's name reads as the roster's; the list's middle initial does not. The
    // federal list's alias is that same name, so it joins the row instead of repeating it.
    expect(facts.names).toEqual([
      { value: 'Sampleperson, Blair Q', sources: ['NY list', 'federal list'] },
    ])
    expect(facts.otherNames).toEqual([
      { value: 'SAMPLEFORD, BLAIR', sources: ['NPPES', 'NY list'] },
    ])
    expect(facts.npis).toEqual([{ value: '1234567893', sources: ['NY list', 'federal list'] }])
    // The roster's NY 012-345 is never repeated; the padded "00012345" is another text, so it
    // stands as its own row, marked with its list.
    expect(facts.licences).toEqual([
      { value: { state: 'NJ', number: 'ab 77' }, sources: ['NPPES', 'federal list'] },
      { value: { state: 'NY', number: '00012345' }, sources: ['NY list'] },
    ])
    expect(facts.places).toEqual([{ value: 'Albany, NY', sources: ['NY list'] }])
    // The registry's taxonomies as before: one code under two licences is two rows, primary first.
    expect(facts.taxonomies.map((item) => [item.code, item.license_state])).toEqual([
      ['207Q00000X', 'NJ'],
      ['207Q00000X', 'ny'],
    ])
  })

  it('adds nothing when no source has anything new, and no taxonomies without the registry', () => {
    const facts = addedFacts({ ...roster, npi: '1234567893' }, null, [
      { caption: 'NY list', record: record({ npi: '1234567893' }) },
    ])
    expect(facts).toEqual({
      names: [],
      otherNames: [],
      npis: [],
      licences: [],
      places: [],
      taxonomies: [],
    })
  })

  it('keeps a numbered registry licence when the roster has only the state', () => {
    const facts = addedFacts(
      { ...roster, licenses: [{ state: 'NY', number: null, kind: null }] },
      { ...registry, taxonomies: [taxonomy({ license_state: 'NY', license_number: '012345' })] },
      [],
    )
    expect(facts.licences).toEqual([
      { value: { state: 'NY', number: '012345' }, sources: ['NPPES'] },
    ])
  })
})

describe('listedRecords', () => {
  const result = (overrides: Partial<Verification>): Verification =>
    ({
      id: 'r',
      provider_id: 'p',
      credential_type: 'exclusion',
      source_id: 'ny_omig',
      outcome: 'completed',
      finding: 'record_found',
      match: { level: 'high', evidence: [], explanation: '', relation: 'party' },
      summary: '',
      details: {},
      provenance: {
        source_id: 'ny_omig',
        source_url: 'https://example.test',
        method: 'bulk_dataset',
        verified_at: '2026-01-01T00:00:00Z',
        source_updated_at: null,
        snapshot_id: null,
        raw_record_ref: null,
      },
      freshness: 'cached',
      created_at: '2026-01-01T00:00:00Z',
      matched_records: [record({})],
      changed_from: null,
      ...overrides,
    }) as Verification

  it('keeps the records of exact and high matches of the party itself', () => {
    const results = [
      result({}),
      result({ match: { level: 'exact', evidence: [], explanation: '', relation: 'party' } }),
      result({ match: { level: 'potential', evidence: [], explanation: '', relation: 'party' } }),
      result({
        match: { level: 'high', evidence: [], explanation: '', relation: 'related_party' },
      }),
      result({ outcome: 'failed', match: null, matched_records: [] }),
    ]
    const listed = listedRecords(results, (id) => `${id} caption`)
    expect(listed.map((item) => item.caption)).toEqual(['ny_omig caption', 'ny_omig caption'])
  })
})

describe('listCaption', () => {
  it('names the federal list, a state list by its state, else the list by name', () => {
    expect(listCaption({ jurisdictions: ['US'], display_name: 'HHS-OIG' }, 'oig_leie')).toBe(
      'federal list',
    )
    expect(listCaption({ jurisdictions: ['NY'], display_name: 'New York OMIG' }, 'ny_omig')).toBe(
      'NY list',
    )
    expect(listCaption({ jurisdictions: ['NY', 'NJ'], display_name: 'Two states' }, 'x')).toBe(
      'Two states',
    )
    expect(listCaption(undefined, 'ny_omig')).toBe('ny_omig')
  })
})

describe('addedTaxonomies', () => {
  it('adds the codes the roster lacks, primary first, each once', () => {
    const added = addedTaxonomies(
      { taxonomy_codes: ['207q00000x'] },
      {
        taxonomies: [
          taxonomy({ code: '207Q00000X', primary: true }),
          taxonomy({ code: '363L00000X', license_state: 'NY', license_number: '1' }),
          taxonomy({ code: '363L00000X', license_state: 'NY', license_number: '1' }),
          taxonomy({ code: '363L00000X', license_state: 'NJ', license_number: '2' }),
          taxonomy({ code: '261QM0801X', primary: true }),
        ],
      },
    )
    expect(added.map((item) => [item.code, item.license_state])).toEqual([
      ['261QM0801X', null],
      ['363L00000X', 'NY'],
      ['363L00000X', 'NJ'],
    ])
    expect(addedTaxonomies({ taxonomy_codes: [] }, { taxonomies: [] })).toEqual([])
  })
})
