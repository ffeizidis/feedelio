/** The one place that knows how to talk to the Feedelio API. */

export interface Health {
  status: string
  version: string
  feeds: number
  entries: number
  unread: number
  broken_feeds: number
}

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  // Defaults first, then the caller's headers — and via Headers, so every
  // HeadersInit form (object, entry list, Headers) merges the same way.
  const headers = new Headers({ Accept: 'application/json' })
  new Headers(init.headers).forEach((value, name) => headers.set(name, value))

  const response = await fetch(`/api${path}`, { ...init, headers })
  if (!response.ok) {
    throw new ApiError(response.status, `${init.method ?? 'GET'} ${path} failed`)
  }
  return (await response.json()) as T
}

export const api = {
  health: () => request<Health>('/health'),
}
