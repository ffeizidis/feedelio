import { expect, test } from '@playwright/test'

/**
 * Smoke test against the real stack: built SPA served by the API, reading a
 * seeded SQLite library. The subscribe → read → mark-read journey is added as
 * those endpoints land in M1.
 */
test.describe('Feedelio smoke', () => {
  test('serves the SPA and shows data from the API', async ({ page }) => {
    await page.goto('/')

    await expect(page).toHaveTitle('Feedelio')
    await expect(page.getByRole('heading', { name: 'Feedelio' })).toBeVisible()

    // Counts come from the seeded feed, so this proves DB → API → browser.
    const unread = page.getByRole('definition').last()
    await expect(unread).toHaveText('2')
    await expect(page.getByRole('alert')).toHaveCount(0)
  })

  test('reports the library through the API', async ({ request }) => {
    const response = await request.get('/api/health')

    expect(response.ok()).toBeTruthy()
    expect(await response.json()).toMatchObject({
      status: 'ok',
      feeds: 1,
      entries: 2,
      unread: 2,
      broken_feeds: 0,
    })
  })

  test('falls back to the SPA on a client-side route', async ({ page }) => {
    const response = await page.goto('/folder/news')

    expect(response?.status()).toBe(200)
    await expect(page.getByRole('heading', { name: 'Feedelio' })).toBeVisible()
  })
})
