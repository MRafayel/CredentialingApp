/** Server state: one query key family per resource, and the hooks screens use. */
import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
  type QueryClient,
} from '@tanstack/react-query'
import {
  get,
  post,
  put,
  type AttentionFilter,
  type HistoryPage,
  type KindFilter,
  type NpiLookup,
  type NpiSearchQuery,
  type Provider,
  type ProviderCreate,
  type ProviderPage,
  type ProviderStatus,
  type ProviderVerdict,
  type RosterOrder,
  type Verification,
} from './api.ts'

export const keys = {
  sources: ['sources'] as const,
  providers: ['providers'] as const,
  providerList: (q: string, page: number, narrowing: RosterNarrowing) =>
    ['providers', 'list', { q, page, ...narrowing }] as const,
  summary: ['providers', 'summary'] as const,
  provider: (id: string) => ['providers', 'detail', id] as const,
  status: (id: string) => ['providers', 'detail', id, 'status'] as const,
  /** Everything read from a provider's verifications: its history pages and its latest NPI. */
  verifications: (id: string) => ['providers', 'detail', id, 'verifications'] as const,
  history: (id: string) => ['providers', 'detail', id, 'verifications', 'history'] as const,
  latestNpi: (id: string) => ['providers', 'detail', id, 'verifications', 'npi', 'latest'] as const,
  verdict: (id: string) => ['providers', 'detail', id, 'verdict'] as const,
  lookup: (q: string, state: string | null) => ['exclusions', 'search', { q, state }] as const,
  record: (id: string) => ['exclusions', 'records', id] as const,
  npi: (npi: string) => ['npi', 'detail', npi] as const,
  npiSearch: (query: NpiSearchQuery) => ['npi', 'search', query] as const,
  suggestions: (id: string) => ['providers', 'detail', id, 'npi-suggestions'] as const,
  importPreview: (text: string) => ['imports', 'preview', text] as const,
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

/** A source's display name by id, from the sources list; the id itself until it loads. */
export function useSourceName(): (id: string) => string {
  const sources = useSources()
  const names = new Map(
    (sources.data ?? []).map((source) => [source.source_id, source.display_name]),
  )
  return (id: string) => names.get(id) ?? id
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

/** What narrows and orders a roster page beyond the search: all the backend's. */
export type RosterNarrowing = {
  attention?: AttentionFilter
  kind?: KindFilter
  order?: RosterOrder
}

/**
 * One page of the roster with each provider's status in the backend's order (those needing a
 * person first unless `order` says newest or oldest); `attention` keeps one reason, `kind`
 * individuals or organizations.
 */
export function useProviderList(q: string, page: number, narrowing: RosterNarrowing = {}) {
  return useQuery({
    queryKey: keys.providerList(q, page, narrowing),
    queryFn: ({ signal }) =>
      get('/providers', {
        query: { q: q || undefined, page, page_size: PAGE_SIZE, ...narrowing },
        signal,
      }),
    placeholderData: keepPreviousData,
  })
}

/** How many providers have each reason for attention, and the roster's total. */
export function useProviderSummary() {
  return useQuery({
    queryKey: keys.summary,
    queryFn: ({ signal }) => get('/providers/summary', { signal }),
  })
}

/** A provider already on a cached list page, so its page can render before it loads. */
function cachedListItem(client: QueryClient, id: string) {
  for (const [, page] of client.getQueriesData<ProviderPage>({ queryKey: ['providers', 'list'] })) {
    const found = page?.items.find((item) => item.id === id)
    if (found) return found
  }
  return undefined
}

function cachedProvider(client: QueryClient, id: string): Provider | undefined {
  return cachedListItem(client, id)
}

/** The provider's monitoring status, as the backend derives it now. */
export function useProviderStatus(id: string) {
  const client = useQueryClient()
  return useQuery<ProviderStatus>({
    queryKey: keys.status(id),
    queryFn: ({ signal }) =>
      get('/providers/{provider_id}/status', { path: { provider_id: id }, signal }),
    placeholderData: () => cachedListItem(client, id)?.status,
  })
}

/**
 * The roster's view of every provider changes with a check, a confirmed NPI or a visit: its list
 * pages, its counts and the provider's own status are fetched again.
 */
function refreshRoster(client: QueryClient, id?: string) {
  void client.invalidateQueries({ queryKey: ['providers', 'list'] })
  void client.invalidateQueries({ queryKey: keys.summary })
  if (id) void client.invalidateQueries({ queryKey: keys.status(id) })
}

/**
 * The provider's page was opened: results changed before now stop reading as changed. The
 * roster and its counts are refetched, so "Changed" has cleared when the person goes back.
 */
export function useMarkSeen(id: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => post('/providers/{provider_id}/seen', { path: { provider_id: id } }),
    onSuccess: () => refreshRoster(client, id),
  })
}

export function useProvider(id: string) {
  const client = useQueryClient()
  return useQuery<Provider>({
    queryKey: keys.provider(id),
    queryFn: ({ signal }) => get('/providers/{provider_id}', { path: { provider_id: id }, signal }),
    placeholderData: () => cachedProvider(client, id),
  })
}

/**
 * The provider's history, newest first, one page of moments at a time (the backend's page size);
 * `fetchNextPage` reads the page before the oldest loaded, until `next_before` is null. The
 * pages stay loaded for the visit.
 */
export function useHistory(id: string) {
  return useInfiniteQuery({
    queryKey: keys.history(id),
    queryFn: ({ pageParam, signal }) =>
      get('/providers/{provider_id}/verifications', {
        path: { provider_id: id },
        query: pageParam ? { before: pageParam } : undefined,
        signal,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_before,
  })
}

/** The provider's newest NPI verification from any source, or null: what the NPI card shows. */
export function useLatestNpiVerification(id: string) {
  return useQuery({
    queryKey: keys.latestNpi(id),
    queryFn: ({ signal }) =>
      get('/providers/{provider_id}/verifications/npi/latest', {
        path: { provider_id: id },
        signal,
      }),
  })
}

/**
 * New results are history at once: they go on top of the newest loaded page (moments are never
 * split, so the refetch that follows puts every page back on the backend's boundaries).
 */
function prependHistory(client: QueryClient, id: string, results: Verification[]) {
  client.setQueryData<InfiniteData<HistoryPage, string | null>>(keys.history(id), (old) => {
    const [first, ...rest] = old?.pages ?? []
    if (!old || !first) return old
    return { ...old, pages: [{ ...first, items: [...results, ...first.items] }, ...rest] }
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

/**
 * The import's dry run: every row of the text with its problems; nothing is stored. Kept only
 * while the sheet shows it (`gcTime` 0), since the text is the customer's roster.
 */
export function useImportPreview(text: string, enabled: boolean) {
  return useQuery({
    queryKey: keys.importPreview(text),
    queryFn: ({ signal }) => post('/providers/import', { body: { text, dry_run: true }, signal }),
    enabled,
    gcTime: 0,
    placeholderData: keepPreviousData,
  })
}

/** Store the importable rows and run their first check; the roster and its counts refetch. */
export function useImportProviders() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (text: string) => post('/providers/import', { body: { text, dry_run: false } }),
    onSuccess: () => refreshRoster(client),
  })
}

export function useAddProvider() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: ProviderCreate) => post('/providers', { body }),
    onSuccess: (provider) => {
      // The backend ran the provider's first check before answering: the roster, its counts
      // and the new row's status are fetched again, without holding the sheet.
      client.setQueryData(keys.provider(provider.id), provider)
      refreshRoster(client, provider.id)
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
      prependHistory(client, id, verdict.results)
      refreshRoster(client, id)
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
      // The new verification is the latest NPI one and the newest history row.
      client.setQueryData<Verification | null>(keys.latestNpi(id), result)
      prependHistory(client, id, [result])
      void client.invalidateQueries({ queryKey: ['npi', 'detail'] })
      refreshRoster(client, id)
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
      client.setQueryData<Verification | null>(keys.latestNpi(id), verification)
      prependHistory(client, id, [verification])
      void client.invalidateQueries({ queryKey: keys.suggestions(id) })
      // The backend rechecked the lists right after the NPI, in the same request: the verdict
      // and the history have new rows.
      void client.invalidateQueries({ queryKey: keys.verdict(id) })
      refreshRoster(client, id)
      return client.invalidateQueries({ queryKey: keys.verifications(id) })
    },
  })
}
