/** Server state: one query key family per resource, and the hooks screens use. */
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query'
import {
  get,
  post,
  put,
  type NpiLookup,
  type NpiSearchQuery,
  type Provider,
  type ProviderCreate,
  type ProviderPage,
  type ProviderVerdict,
  type Verification,
} from './api.ts'

export const keys = {
  sources: ['sources'] as const,
  providers: ['providers'] as const,
  providerList: (q: string, page: number) => ['providers', 'list', { q, page }] as const,
  provider: (id: string) => ['providers', 'detail', id] as const,
  verifications: (id: string) => ['providers', 'detail', id, 'verifications'] as const,
  verdict: (id: string) => ['providers', 'detail', id, 'verdict'] as const,
  lookup: (q: string, state: string | null) => ['exclusions', 'search', { q, state }] as const,
  record: (id: string) => ['exclusions', 'records', id] as const,
  npi: (npi: string) => ['npi', 'detail', npi] as const,
  npiSearch: (query: NpiSearchQuery) => ['npi', 'search', query] as const,
  suggestions: (id: string) => ['providers', 'detail', id, 'npi-suggestions'] as const,
}

export const PAGE_SIZE = 10

/** How many lookup matches one search shows (the backend allows up to 50). */
export const LOOKUP_LIMIT = 25

export function useSources() {
  return useQuery({
    queryKey: keys.sources,
    queryFn: ({ signal }) => get('/sources', { signal }),
  })
}

/**
 * A direct exclusion lookup; `q` is already trimmed and long enough, or the query waits.
 * `state` narrows it to the records the backend ties to that state.
 */
export function useExclusionLookup(q: string, state: string | null, enabled: boolean) {
  return useQuery({
    queryKey: keys.lookup(q, state),
    queryFn: ({ signal }) =>
      get('/exclusions/search', {
        query: state ? { q, limit: LOOKUP_LIMIT, state } : { q, limit: LOOKUP_LIMIT },
        signal,
      }),
    enabled,
    placeholderData: keepPreviousData,
  })
}

/** One exclusion record and what the stored lists hold around it. */
export function useRecordProfile(id: string) {
  return useQuery({
    queryKey: keys.record(id),
    queryFn: ({ signal }) =>
      get('/exclusions/records/{record_id}', { path: { record_id: id }, signal }),
  })
}

export function useProviderList(q: string, page: number) {
  return useQuery({
    queryKey: keys.providerList(q, page),
    queryFn: ({ signal }) =>
      get('/providers', { query: { q: q || undefined, page, page_size: PAGE_SIZE }, signal }),
    placeholderData: keepPreviousData,
  })
}

/** A provider already on a cached list page, so its page can render before it loads. */
function cachedProvider(client: QueryClient, id: string): Provider | undefined {
  for (const [, page] of client.getQueriesData<ProviderPage>({ queryKey: ['providers', 'list'] })) {
    const found = page?.items.find((item) => item.id === id)
    if (found) return found
  }
  return undefined
}

export function useProvider(id: string) {
  const client = useQueryClient()
  return useQuery<Provider>({
    queryKey: keys.provider(id),
    queryFn: ({ signal }) => get('/providers/{provider_id}', { path: { provider_id: id }, signal }),
    placeholderData: () => cachedProvider(client, id),
  })
}

export function useVerifications(id: string) {
  return useQuery({
    queryKey: keys.verifications(id),
    queryFn: ({ signal }) =>
      get('/providers/{provider_id}/verifications', { path: { provider_id: id }, signal }),
  })
}

/** The backend's verdict with the latest result of each source: the hero and the cards. */
export function useVerdict(id: string) {
  return useQuery({
    queryKey: keys.verdict(id),
    queryFn: ({ signal }) =>
      get('/providers/{provider_id}/verdict', { path: { provider_id: id }, signal }),
  })
}

export function useAddProvider() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: ProviderCreate) => post('/providers', { body }),
    onSuccess: (provider) => {
      client.setQueryData(keys.provider(provider.id), provider)
      return client.invalidateQueries({ queryKey: ['providers', 'list'] })
    },
  })
}

export function useRunCheck(id: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () =>
      post('/providers/{provider_id}/verifications/exclusions', { path: { provider_id: id } }),
    onSuccess: (verdict) => {
      // The run answers with the new verdict, so it replaces the cached one as it is.
      client.setQueryData<ProviderVerdict>(keys.verdict(id), verdict)
      // Its results are history at once; the refetch then confirms them.
      client.setQueryData<Verification[]>(keys.verifications(id), (old) => [
        ...verdict.results,
        ...(old ?? []),
      ])
      return client.invalidateQueries({ queryKey: keys.verifications(id) })
    },
  })
}

// --- The NPI registry (P1) ------------------------------------------------------------------

/**
 * One NPI from the registry mirror, with the backend's status and the mirror's load. The
 * backend asks the live registry on its own only when the mirror has no row or is stale; a
 * reader who wants a fresh answer uses `useCheckNpiLive`.
 */
export function useNpi(npi: string, enabled = true) {
  return useQuery({
    queryKey: keys.npi(npi),
    queryFn: ({ signal }) => get('/npi/{npi}', { path: { npi }, signal }),
    enabled: enabled && /^\d{10}$/.test(npi),
  })
}

/** One request to the live registry for this NPI; its answer replaces the cached lookup. */
export function useCheckNpiLive(npi: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => get('/npi/{npi}', { path: { npi }, query: { live: true } }),
    onSuccess: (lookup) => {
      client.setQueryData<NpiLookup>(keys.npi(npi), lookup)
    },
  })
}

/**
 * The registry search: the mirror by exact name key (`q`), or the live registry with
 * `live: true` and the structured fields. `enabled` gates it, so a half-typed query waits.
 */
export function useNpiSearch(query: NpiSearchQuery, enabled: boolean) {
  return useQuery({
    queryKey: keys.npiSearch(query),
    queryFn: ({ signal }) => get('/npi/search', { query, signal }),
    enabled,
    placeholderData: keepPreviousData,
  })
}

/** NPIs the mirror suggests for a provider without one, best first; read only. */
export function useNpiSuggestions(id: string, enabled = true) {
  return useQuery({
    queryKey: keys.suggestions(id),
    queryFn: ({ signal }) =>
      get('/providers/{provider_id}/npi-suggestions', { path: { provider_id: id }, signal }),
    enabled,
  })
}

/** Verify the provider's NPI in NPPES (`live` asks the registry itself); one more history row. */
export function useVerifyNpi(id: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ live }: { live: boolean }) =>
      post('/providers/{provider_id}/verifications/npi', {
        path: { provider_id: id },
        query: live ? { live: true } : undefined,
      }),
    onSuccess: (result) => {
      client.setQueryData<Verification[]>(keys.verifications(id), (old) => [result, ...(old ?? [])])
      void client.invalidateQueries({ queryKey: ['npi', 'detail'] })
      return client.invalidateQueries({ queryKey: keys.verifications(id) })
    },
  })
}

/**
 * Give a provider without an NPI the one the user confirmed. The answer carries the provider
 * as it now is and the NPI verification the backend appended, so both caches are replaced at
 * once; the suggestions and the roster list are refetched.
 */
export function useSetProviderNpi(id: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (npi: string) =>
      put('/providers/{provider_id}/npi', { path: { provider_id: id }, body: { npi } }),
    onSuccess: ({ provider, verification }) => {
      client.setQueryData<Provider>(keys.provider(id), provider)
      client.setQueryData<Verification[]>(keys.verifications(id), (old) => [
        verification,
        ...(old ?? []),
      ])
      void client.invalidateQueries({ queryKey: keys.suggestions(id) })
      void client.invalidateQueries({ queryKey: ['providers', 'list'] })
      return client.invalidateQueries({ queryKey: keys.verifications(id) })
    },
  })
}
