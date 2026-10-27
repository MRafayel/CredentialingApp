/**
 * The single door to the backend. Components never call fetch directly.
 *
 * Paths, parameters and bodies are typed from `api-schema.d.ts`, which `make api-types`
 * generates from the backend's OpenAPI schema; never edit that file by hand.
 */
import type { components, paths } from './api-schema.d.ts'

type Schemas = components['schemas']
export type Provider = Schemas['ProviderResponse']
export type ProviderPage = Schemas['ProviderPageResponse']
export type ProviderCreate = Schemas['ProviderCreate']
export type Verification = Schemas['VerificationResponse']
export type ProviderVerdict = Schemas['VerdictResponse']
export type MatchedRecord = Schemas['MatchedRecordResponse']
export type SourceStatus = Schemas['SourceStatusResponse']
export type Lookup = Schemas['LookupResponse']
export type LookupRecord = Schemas['LookupRecordResponse']
export type SearchedSource = Schemas['SearchedSourceResponse']
export type ExclusionAction = LookupRecord['action']
export type Outcome = Verification['outcome']
export type Freshness = Verification['freshness']
export type SourceHealth = SourceStatus['health']

/** The seeded development organization; override with VITE_ORGANIZATION_ID. */
const DEFAULT_ORGANIZATION_ID = '00000000-0000-0000-0000-000000000001'
const ORGANIZATION_ID = import.meta.env.VITE_ORGANIZATION_ID || DEFAULT_ORGANIZATION_ID

type Method = 'get' | 'post'
type PathsWith<M extends Method> = {
  [P in keyof paths]: undefined extends paths[P][M] ? never : P
}[keyof paths]
type Operation<P extends keyof paths, M extends Method> = NonNullable<paths[P][M]>

type JsonOf<R> = R extends { content: { 'application/json': infer B } } ? B : never
type SuccessBody<O> = O extends { responses: infer R }
  ? { [K in keyof R]: K extends 200 | 201 ? JsonOf<R[K]> : never }[keyof R]
  : never

type PathParams<O> = O extends { parameters: { path: infer A } } ? A : never
type QueryParams<O> = O extends { parameters: { query?: infer Q } } ? Q : never
type RequestBody<O> = O extends { requestBody: { content: { 'application/json': infer B } } }
  ? B
  : never

type Options<O> = { signal?: AbortSignal } & ([PathParams<O>] extends [never]
  ? { path?: never }
  : { path: PathParams<O> }) &
  ([QueryParams<O>] extends [never] ? { query?: never } : { query?: QueryParams<O> })

/** Options are required exactly when the path has parameters to fill. */
type Args<O, Extra = object> = [PathParams<O>] extends [never]
  ? [options?: Options<O> & Extra]
  : [options: Options<O> & Extra]

/** A request the backend refused or could not answer; `message` is the backend's own words. */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** The backend's `detail`: a sentence, or FastAPI's list of field errors. */
export function detailMessage(body: unknown): string | null {
  if (typeof body !== 'object' || body === null || !('detail' in body)) return null
  const { detail } = body
  if (typeof detail === 'string') return detail
  if (!Array.isArray(detail)) return null
  const lines = detail.flatMap((item: unknown) => {
    if (typeof item !== 'object' || item === null || !('msg' in item)) return []
    const msg = typeof item.msg === 'string' ? item.msg : ''
    const loc =
      'loc' in item && Array.isArray(item.loc)
        ? item.loc.filter((part: unknown) => part !== 'body').join('.')
        : ''
    return [loc ? `${loc}: ${msg}` : msg]
  })
  return lines.length ? lines.join('; ') : null
}

function buildUrl(template: string, path?: object, query?: object): string {
  const filled = template.replace(/\{(\w+)\}/g, (_, name: string) => {
    const value: unknown = path ? (path as Record<string, unknown>)[name] : undefined
    return encodeURIComponent(String(value))
  })
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const suffix = search.size ? `?${search}` : ''
  return `/api${filled}${suffix}`
}

async function request<T>(method: Method, url: string, body: unknown, signal?: AbortSignal) {
  let response: Response
  try {
    response = await fetch(url, {
      method: method.toUpperCase(),
      signal,
      headers: {
        Accept: 'application/json',
        'X-Organization-Id': ORGANIZATION_ID,
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (error) {
    if (signal?.aborted) throw error
    throw new ApiError(0, 'The API could not be reached.')
  }
  const payload: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    throw new ApiError(
      response.status,
      detailMessage(payload) ?? `The API answered with status ${response.status}.`,
    )
  }
  return payload as T
}

export function get<P extends PathsWith<'get'>>(
  path: P,
  ...[options]: Args<Operation<P, 'get'>>
): Promise<SuccessBody<Operation<P, 'get'>>> {
  return request('get', buildUrl(path, options?.path, options?.query), undefined, options?.signal)
}

export function post<P extends PathsWith<'post'>>(
  path: P,
  ...[options]: Args<Operation<P, 'post'>, { body?: RequestBody<Operation<P, 'post'>> }>
): Promise<SuccessBody<Operation<P, 'post'>>> {
  const url = buildUrl(path, options?.path, options?.query)
  return request('post', url, options?.body, options?.signal)
}
