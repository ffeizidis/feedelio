import { expect, test } from '@playwright/test'

/**
 * Mixed scenario: "subscribe to a feed and check it shows up."
 *
 * Subscribing has no UI yet (#17), so the act goes through the API — but the
 * consequence is checked in the browser, which is the part a person would
 * notice. Rewrite the first half as UI clicks once the shell lands; the
 * assertions below survive unchanged.
 *
 * The feed is a local fixture resolved through FEEDELIO_FEED_ROOT, never the
 * live network: a network feed makes the scenario slow and its verdict a
 * coin toss.
 */
test('a newly subscribed feed reaches the browser', async ({ page, request }) => {
  const before = await (await request.get('/api/health')).json()

  // 409 means an earlier run in this same library already subscribed it, which
  // is just as healthy as 201 — a scenario that goes red on its second run
  // reports the runner's state, not the app's.
  const created = await request.post('/api/feeds', { data: { url: 'sample.rss' } })
  expect([201, 409]).toContain(created.status())
  const subscribedNow = created.status() === 201
  if (subscribedNow) {
    expect(await created.json()).toMatchObject({
      url: 'sample.rss',
      title: 'Feedelio RSS 2.0 Feed',
      version: 'rss20',
      broken: false,
    })
  }

  const feeds = await (await request.get('/api/feeds')).json()
  expect(feeds.map((feed: { url: string }) => feed.url)).toContain('sample.rss')

  // Its articles came with it — a subscription with no entries is not a
  // working subscription.
  const entries = await (await request.get('/api/entries?feed=sample.rss')).json()
  expect(entries.length).toBeGreaterThan(0)
  expect(entries[0]).toMatchObject({ feed_url: 'sample.rss', read: false })

  // And the browser sees the new state after a reload.
  await page.goto('/')
  const expected = before.feeds + (subscribedNow ? 1 : 0)
  await expect(page.getByRole('definition').nth(1)).toHaveText(String(expected))
})
