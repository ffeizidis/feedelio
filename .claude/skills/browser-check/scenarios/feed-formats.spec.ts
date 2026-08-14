import { expect, test } from '@playwright/test'

/**
 * API scenario for #10: "all three syndication formats fetch, parse and list."
 *
 * `tests/` proves this against the core in-process; here it goes over HTTP to a
 * running server, through the real ASGI stack — a fixture that parses in a unit
 * test but 500s through the API would pass there and fail here.
 *
 * Fixtures resolve through FEEDELIO_FEED_ROOT, so nothing touches the network.
 * There is no subscribe UI yet (#17): every assertion below is API-only.
 */
const FORMATS = [
  { url: 'sample.atom', title: 'Feedelio Test Feed', version: 'atom10' },
  { url: 'sample.rss', title: 'Feedelio RSS 2.0 Feed', version: 'rss20' },
  { url: 'sample.rdf', title: 'Feedelio RSS 1.0 Feed', version: 'rss10' },
]

/** A path under the feed root with no file behind it — a plausible typo. */
const MISSING = 'not-a-feed.xml'

test('every supported format subscribes, parses and lists its entries', async ({ request }) => {
  for (const { url, title, version } of FORMATS) {
    // 409 means an earlier scenario (or an earlier pass) already has it, which
    // says as much about a working subscription as 201 does.
    const created = await request.post('/api/feeds', { data: { url } })
    expect([201, 409], `subscribing to ${url}`).toContain(created.status())
    if (created.status() === 201) {
      expect(await created.json()).toMatchObject({ url, title, version, broken: false })
    }
  }

  // The listing is the sidebar's source of truth: title and version have to
  // survive the round trip through SQLite, not just the parse.
  const feeds = await (await request.get('/api/feeds')).json()
  const byUrl = new Map(feeds.map((feed: { url: string }) => [feed.url, feed]))
  for (const { url, title, version } of FORMATS) {
    expect(byUrl.get(url), `${url} missing from /api/feeds`).toMatchObject({
      title,
      version,
      broken: false,
    })
  }

  // A subscription with no articles is not a working subscription, and each
  // format has to yield readable bodies, not just headlines.
  for (const { url, title } of FORMATS) {
    const entries = await (await request.get(`/api/entries?feed=${url}`)).json()
    expect(entries.length, `no entries for ${url}`).toBeGreaterThan(0)
    for (const entry of entries) {
      expect(entry).toMatchObject({ feed_url: url, feed_title: title })
      expect(entry.title).toBeTruthy()
      expect(entry.content).toBeTruthy()
    }
  }
})

test('a duplicate is a 409 and a feed that will not fetch is a 400 that leaks no path', async ({
  request,
}) => {
  const duplicate = await request.post('/api/feeds', { data: { url: 'sample.atom' } })
  expect(duplicate.status()).toBe(409)
  expect((await duplicate.json()).detail).toContain('sample.atom')

  const unfetchable = await request.post('/api/feeds', { data: { url: MISSING } })
  expect(unfetchable.status()).toBe(400)
  const detail: string = (await unfetchable.json()).detail

  // The server resolves a feed URL against FEEDELIO_FEED_ROOT, so the parse
  // error underneath carries an absolute path on this machine. The client must
  // not see it: no separator, no filesystem words, no exception spill.
  expect(detail).toContain(MISSING)
  expect(detail).not.toContain('/')
  expect(detail).not.toMatch(/traceback|errno|no such file|permission denied/i)

  // And the typo left nothing behind — a failed fetch must not become a
  // permanently broken subscription.
  const feeds = await (await request.get('/api/feeds')).json()
  expect(feeds.map((feed: { url: string }) => feed.url)).not.toContain(MISSING)
})
