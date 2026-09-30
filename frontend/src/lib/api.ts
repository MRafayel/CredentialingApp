/** The single door to the backend. Components never call fetch directly. */

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, path: string) {
    super(`API request to ${path} failed with status ${status}`)
    this.name = 'ApiError'
    this.status = status
  }
}

export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api${path}`, {
    signal,
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) {
    throw new ApiError(response.status, path)
  }
  return (await response.json()) as T
}
