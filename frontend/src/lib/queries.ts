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
  lookup: (q: string) => ['exclusions', 'search', q] as const,
  record: (id: string) => ['exclusions', 'records', id] as const,
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

/** A direct exclusion lookup; `q` is already trimmed and long enough, or the query waits. */
export function useExclusionLookup(q: string, enabled: boolean) {
  return useQuery({
    queryKey: keys.lookup(q),
    queryFn: ({ signal }) =>
      get('/exclusions/search', { query: { q, limit: LOOKUP_LIMIT }, signal }),
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
