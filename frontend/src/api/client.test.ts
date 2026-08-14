import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api, request } from './client'

afterEach(() => vi.restoreAllMocks())

/** Mock fetch and return the headers it was called with. */
async function headersSentBy(init?: RequestInit): Promise<Headers> {
  const fetchMock = vi
    .spyOn(globalThis, 'fetch')
    .mockResolvedValue({ ok: true, json: () => Promise.resolve({}) } as Response)

  await request('/health', init)
  return new Headers(fetchMock.mock.calls[0][1]?.headers)
}

describe('api client', () => {
  it('calls the API under /api and parses JSON', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue({ ok: true, json: () => Promise.resolve({ status: 'ok' }) } as Response)

    await expect(api.health()).resolves.toEqual({ status: 'ok' })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/health')
  })

  it('sends the JSON Accept header by default', async () => {
    expect((await headersSentBy()).get('Accept')).toBe('application/json')
  })

  it('keeps its defaults when the caller adds headers', async () => {
    const headers = await headersSentBy({
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    })

    expect(headers.get('Accept')).toBe('application/json')
    expect(headers.get('Content-Type')).toBe('application/json')
  })

  it('lets the caller override a default, whatever form the headers take', async () => {
    const headers = await headersSentBy({ headers: new Headers({ Accept: 'text/plain' }) })

    expect(headers.get('Accept')).toBe('text/plain')
  })

  it('turns a failed response into an ApiError carrying the status', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({ ok: false, status: 500 } as Response)

    await expect(api.health()).rejects.toBeInstanceOf(ApiError)
    await expect(api.health()).rejects.toMatchObject({ status: 500 })
  })
})
