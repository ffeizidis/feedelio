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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    headers: { Accept: 'application/json', ...init?.headers },
    ...init,
  })
  if (!response.ok) {
    throw new ApiError(response.status, `${init?.method ?? 'GET'} ${path} failed`)
  }
  return (await response.json()) as T
}

export const api = {
  health: () => request<Health>('/health'),
}
