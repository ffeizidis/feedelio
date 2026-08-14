import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'

/**
 * API-only scenarios for #15: the way in from Inoreader and the way back out.
 *
 * Everything here goes over HTTP against the running server — the upload is a
 * real multipart request and the download is a real response with headers,
 * neither of which the in-process tests in `tests/` exercise. There is no
 * import/export UI yet (#17), so nothing below is browser-verified.
 *
 * The fixture's feeds are `https://` URLs that do not resolve, which is safe:
 * an import never fetches. Their titles stay empty until the worker collects
 * them, so the assertions are about URLs and folders only.
 */
const ROOT = process.env.BROWSER_CHECK_ROOT
if (!ROOT) {
  throw new Error('BROWSER_CHECK_ROOT is not set — run scenarios through run.sh.')
}
const OPML = readFileSync(`${ROOT}/tests/fixtures/inoreader.opml`)

/** What the fixture says the library should look like after an import. */
const EXPECTED: Record<string, string[]> = {
  News: ['https://example.com/atom.xml', 'https://rss.example.com/feed.xml'],
  // Nested under Tech in the file: a feed goes into its *innermost* category.
  Rust: ['https://this-week-in-rust.org/rss.xml'],
  // A category with no feeds still becomes a folder — the file says it is there.
  'Saved for later': [],
  Tech: ['https://rdf.example.com/feed.rdf'],
}
const UNCATEGORISED = 'https://blog.example.com/index.xml'
const FEEDS_IN_FIXTURE = 5

type Tree = { name: string; feeds: { url: string }[] }[]

const upload = (buffer: Buffer, name: string) => ({
  multipart: { file: { name, mimeType: 'text/x-opml', buffer } },
})

/** The imported folders only: other scenarios own the rest of the tree. */
function imported(tree: Tree): Record<string, string[]> {
  const seen: Record<string, string[]> = {}
  for (const folder of tree) {
    if (folder.name in EXPECTED) {
      seen[folder.name] = folder.feeds.map((feed) => feed.url).sort()
    }
  }
  return seen
}

test('an Inoreader export becomes folders, and a re-import does not reorganise them', async ({
  request,
}) => {
  const response = await request.post('/api/opml', upload(OPML, 'inoreader.opml'))
  expect(response.status()).toBe(200)
  const summary = await response.json()

  // On a second pass everything is already there; either way the file's five
  // feeds are accounted for and none of its categories was unusable.
  expect(summary.failed).toEqual([])
  expect(summary.folders_skipped).toEqual([])
  expect(summary.added + summary.already_present).toBe(FEEDS_IN_FIXTURE)

  const tree: Tree = await (await request.get('/api/folders')).json()
  expect(imported(tree)).toEqual(EXPECTED)

  // A feed with no category stays unfiled rather than being invented a home.
  const unfiled = tree.find((folder) => folder.name === '')
  expect(unfiled, `no unfiled group in ${JSON.stringify(tree)}`).toBeTruthy()
  expect(unfiled!.feeds.map((feed) => feed.url)).toContain(UNCATEGORISED)

  // -- a re-import must not re-file what you already arranged ----------------
  const moved = await request.put('/api/feeds/folder', {
    data: { url: UNCATEGORISED, folder: 'News' },
  })
  expect(moved.status()).toBe(204)

  const again = await request.post('/api/opml', upload(OPML, 'inoreader.opml'))
  expect(again.status()).toBe(200)
  expect(await again.json()).toMatchObject({
    added: 0,
    already_present: FEEDS_IN_FIXTURE,
    folders_created: [],
  })

  const afterReimport: Tree = await (await request.get('/api/folders')).json()
  const news = afterReimport.find((folder) => folder.name === 'News')!
  expect(
    news.feeds.map((feed) => feed.url),
    'the re-import moved a feed the library already had',
  ).toContain(UNCATEGORISED)

  // Put it back, so the scenario leaves the library as it found it.
  expect(
    (await request.put('/api/feeds/folder', { data: { url: UNCATEGORISED, folder: '' } })).status(),
  ).toBe(204)
})

test('the subscription list downloads as a file and imports back into the same tree', async ({
  request,
}) => {
  const before = imported(await (await request.get('/api/folders')).json())
  expect(before, 'run this after the import scenario, not on its own').toEqual(EXPECTED)

  const exported = await request.get('/api/opml')
  expect(exported.status()).toBe(200)

  // A browser only offers "Save as" when both of these are right.
  expect(exported.headers()['content-type']).toBe('application/xml; charset=utf-8')
  expect(exported.headers()['content-disposition']).toMatch(
    /^attachment; filename="feedelio-subscriptions-\d{4}-\d{2}-\d{2}\.opml"$/,
  )

  const body = await exported.body()
  const text = body.toString('utf-8')
  expect(text).toContain('<outline text="News" title="News">')
  // The empty folder is in the file too, or a round trip would quietly drop it.
  expect(text).toContain('<outline text="Saved for later" title="Saved for later" />')

  // The point of the format: what came out goes back in unchanged. Every feed
  // is already subscribed, so a correct file adds nothing and moves nothing.
  const reimport = await request.post('/api/opml', upload(body, 'feedelio-subscriptions.opml'))
  expect(reimport.status()).toBe(200)
  expect(await reimport.json()).toMatchObject({
    added: 0,
    failed: [],
    folders_created: [],
    folders_skipped: [],
  })

  const after = imported(await (await request.get('/api/folders')).json())
  expect(after, 'the exported file does not round-trip').toEqual(before)
})
