import { expect, test } from '@playwright/test'

/**
 * API-only scenario: "put two feeds in a folder and read the merged stream."
 *
 * Nothing here is reachable in the browser today — the SPA is still the
 * placeholder shell — so the whole scenario drives the JSON API. That is a
 * real verdict about the app, and it is honest about what was checked: the
 * folder tree works, the sidebar that renders it does not exist yet.
 *
 * State is set up from tests/fixtures via FEEDELIO_FEED_ROOT. Scenarios share
 * one library within a run, so anything that might already be there is
 * tolerated (409) rather than assumed absent.
 */
const FOLDER = 'Fixtures'
const IN_FOLDER = ['sample.atom', 'sample.rdf']

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
  expect((await request.delete(`/api/folders/${FOLDER}`)).status()).toBe(204)
})
