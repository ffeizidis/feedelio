import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import type { Health } from './api/client'

const HEALTH: Health = {
  status: 'ok',
  version: '0.1.0',
  feeds: 3,
  entries: 42,
  unread: 7,
  broken_feeds: 0,
}

function mockFetch(response: Partial<Response> & { json?: () => Promise<unknown> }) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue(response as Response)
}

afterEach(() => vi.restoreAllMocks())

describe('App', () => {
  it('shows the library status once the API answers', async () => {
    mockFetch({ ok: true, json: () => Promise.resolve(HEALTH) })

    render(<App />)
    expect(screen.getByRole('status')).toHaveTextContent('Connecting')

    expect(await screen.findByText('7')).toBeInTheDocument()
    expect(screen.getByText('0.1.0')).toBeInTheDocument()
    expect(globalThis.fetch).toHaveBeenCalledWith('/api/health', expect.anything())
  })

  it('reports an unreachable API instead of hanging', async () => {
    mockFetch({ ok: false, status: 503 })

    render(<App />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Cannot reach the API.')
  })
})
