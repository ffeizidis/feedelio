import { expect, test } from '@playwright/test'

/**
 * API-only scenarios for #16: the folder tree — merged streams, one folder per
 * feed, rename, delete, and the unfiled group.
 *
 * Nothing here is reachable in the browser today — the SPA is still the
 * placeholder shell — so the whole file drives the JSON API. That is a real
 * verdict about the app, and it is honest about what was checked: the folder
 * tree works, the sidebar that renders it does not exist yet.
 *
 * State is set up from tests/fixtures via FEEDELIO_FEED_ROOT. Scenarios share
 * one library within a run, so anything that might already be there is
 * tolerated (409, or a delete-first) rather than assumed absent.
 */
const FOLDER = 'Fixtures'
const IN_FOLDER = ['sample.atom', 'sample.rdf']

/** Names used only by the rename scenario, so nothing else can collide. */
const BEFORE = 'Fixtures Archive'
const AFTER = 'Fixtures Attic'

const urls = (feeds: { url: string }[]) => feeds.map((feed) => feed.url).sort()
const path = (name: string) => `/api/folders/${encodeURIComponent(name)}`

test('a folder reads as one merged stream, and a feed lives in exactly one', async ({
  request,
}) => {
  // -- set up ---------------------------------------------------------------
  const subscribed = await request.post('/api/feeds', { data: { url: 'sample.rdf' } })
  expect([201, 409]).toContain(subscribed.status())

  const folder = await request.post('/api/folders', { data: { name: FOLDER } })
  expect([201, 409]).toContain(folder.status())

  for (const url of IN_FOLDER) {
    const moved = await request.put('/api/feeds/folder', { data: { url, folder: FOLDER } })
    expect(moved.status()).toBe(204)
  }

  // -- the folder holds what we put in it ------------------------------------
  const folders = await (await request.get('/api/folders')).json()
  const fixtures = folders.find((f: { name: string }) => f.name === FOLDER)
  expect(fixtures, `no folder named ${FOLDER} in ${JSON.stringify(folders)}`).toBeTruthy()
  expect(fixtures.feeds.map((f: { url: string }) => f.url).sort()).toEqual(IN_FOLDER)

  // -- the merged stream ----------------------------------------------------
  const counts = await Promise.all(
    IN_FOLDER.map(async (url) => {
      const entries = await (await request.get(`/api/entries?feed=${url}`)).json()
      return entries.length
    }),
  )
  const merged = await (await request.get(`/api/entries?folder=${FOLDER}`)).json()

  // Every article from both feeds, and nothing from anywhere else.
  expect(merged.length).toBe(counts[0] + counts[1])
  const feedUrls = [...new Set(merged.map((e: { feed_url: string }) => e.feed_url))]
  expect(feedUrls.sort()).toEqual(IN_FOLDER)
  expect(merged.length).toBeGreaterThan(counts[0]) // genuinely merged, not one feed

  // -- exactly one folder ---------------------------------------------------
  // Moving a feed out has to take it out; "one folder per feed" is only true
  // if the previous membership disappears.
  const out = await request.put('/api/feeds/folder', { data: { url: 'sample.rdf', folder: '' } })
  expect(out.status()).toBe(204)

  const narrowed = await (await request.get(`/api/entries?folder=${FOLDER}`)).json()
  expect([...new Set(narrowed.map((e: { feed_url: string }) => e.feed_url))]).toEqual([
    'sample.atom',
  ])

  const unfiled = await (await request.get('/api/entries?folder=')).json()
  expect(unfiled.map((e: { feed_url: string }) => e.feed_url)).toContain('sample.rdf')

  // -- tidy up --------------------------------------------------------------
  expect((await request.delete(path(FOLDER))).status()).toBe(204)
})

test('a folder can be renamed and deleted without losing the feeds in it', async ({ request }) => {
  // Start from a known tree: an earlier pass may have left these behind, and
  // renaming onto a name that already exists is a 409 by design.
  for (const name of [BEFORE, AFTER]) {
    expect([204, 404]).toContain((await request.delete(path(name))).status())
  }

  expect((await request.post('/api/folders', { data: { name: BEFORE } })).status()).toBe(201)
  const filed = await request.put('/api/feeds/folder', {
    data: { url: 'sample.rss', folder: BEFORE },
  })
  expect(filed.status()).toBe(204)

  // -- rename: the feeds come with it ---------------------------------------
  const renamed = await request.patch(path(BEFORE), { data: { name: AFTER } })
  expect(renamed.status()).toBe(200)
  const body = await renamed.json()
  expect(body.name).toBe(AFTER)
  expect(urls(body.feeds)).toEqual(['sample.rss'])

  const renamedTree = await (await request.get('/api/folders')).json()
  const names = renamedTree.map((folder: { name: string }) => folder.name)
  expect(names, 'the old name survived the rename').not.toContain(BEFORE)
  expect(names).toContain(AFTER)
  expect(urls(renamedTree.find((f: { name: string }) => f.name === AFTER).feeds)).toEqual([
    'sample.rss',
  ])

  // Its articles are still reachable under the new name — a rename that keeps
  // the feed but loses the stream would read as a working rename in the tree.
  const stream = await (
    await request.get(`/api/entries?folder=${encodeURIComponent(AFTER)}`)
  ).json()
  expect(stream.length).toBeGreaterThan(0)
  expect([...new Set(stream.map((e: { feed_url: string }) => e.feed_url))]).toEqual(['sample.rss'])

  // -- delete: the folder goes, the subscriptions stay -----------------------
  expect((await request.delete(path(AFTER))).status()).toBe(204)

  const feeds = await (await request.get('/api/feeds')).json()
  expect(urls(feeds), 'deleting a folder unsubscribed its feeds').toContain('sample.rss')

  const tree = await (await request.get('/api/folders')).json()
  expect(tree.map((folder: { name: string }) => folder.name)).not.toContain(AFTER)

  // -- the unfiled group ----------------------------------------------------
  // Unfiled is a real group with an empty name, so the sidebar can render it
  // like any other and hand the name straight back as `?folder=`.
  const unfiled = tree.find((folder: { name: string }) => folder.name === '')
  expect(unfiled, `no unfiled group in ${JSON.stringify(tree)}`).toBeTruthy()
  expect(urls(unfiled.feeds)).toContain('sample.rss')

  const unfiledStream = await (await request.get('/api/entries?folder=')).json()
  expect(unfiledStream.map((e: { feed_url: string }) => e.feed_url)).toContain('sample.rss')
})
