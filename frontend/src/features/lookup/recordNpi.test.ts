import { describe, expect, it } from 'vitest'
import type { NpiLink, NpiSummary } from '../../lib/api.ts'
import { recordNpi } from './recordNpi.ts'

const summary = (npi: string, status: NpiSummary['status'] = 'active'): NpiSummary => ({
  npi,
  entity_kind: 'individual',
  status,
  person_name: { last: 'Testperson', first: 'Avery', middle: null, suffix: null },
  organization_name: null,
  credential: null,
  primary_taxonomy: null,
  practice_city: 'Sampleville',
  practice_state: 'TX',
  enumeration_date: null,
  deactivation_date: status === 'deactivated' ? '2020-01-02' : null,
  in_file: true,
})

const link = (npi: string | null, level: NpiLink['level']): NpiLink => ({
  npi,
  level,
  evidence: [],
  explanation: npi ? 'A match.' : 'No match: no candidate with this name.',
  candidates: 1,
  linked_at: '2026-11-08T13:15:10Z',
  registry: npi ? summary(npi) : null,
})

const profile = (npi: string | null, links: NpiLink[]) => ({
  record: { npi } as never,
  npi_links: links,
})

describe('recordNpi', () => {
  it('leads with the registry row of a published NPI', () => {
    const own = link('1234567893', 'exact')
    expect(recordNpi(profile('1234567893', [own]))).toEqual({
      kind: 'published',
      npi: '1234567893',
      link: own,
    })
  })

  it('says a published NPI is not in NPPES when its one link row points at nothing', () => {
    const none = link(null, 'none')
    expect(recordNpi(profile('1234567893', [none]))).toEqual({
      kind: 'not_in_nppes',
      npi: '1234567893',
      link: none,
    })
  })

  it('keeps a published NPI as it stands when it was never linked', () => {
    expect(recordNpi(profile('1234567893', []))).toEqual({
      kind: 'unlinked',
      published: '1234567893',
    })
  })

  it('leads a record without an NPI with its best link and keeps the rest in order', () => {
    const best = link('1234567893', 'potential')
    const second = link('1245319599', 'ambiguous')
    const third = link('1003000126', 'ambiguous')
    expect(recordNpi(profile(null, [best, second, third]))).toEqual({
      kind: 'candidates',
      best,
      others: [second, third],
    })
  })

  it('gives the reason when nothing links, and nothing when never linked', () => {
    const none = link(null, 'none')
    expect(recordNpi(profile(null, [none]))).toEqual({ kind: 'no_candidate', link: none })
    expect(recordNpi(profile(null, []))).toEqual({ kind: 'unlinked', published: null })
  })
})
