import { expect, test } from '@playwright/test'

/**
 * Browser scenario: "does the app actually come up in a browser, showing real
 * data from the database?"
 *
 * Everything asserted here is visible on screen. The expected numbers are read
 * from the API rather than hard-coded, so the scenario stays true whatever else
 * is in the seeded library — and comparing the two is the point: it proves the
 * SQLite -> API -> React path end to end.
 */
test('the SPA renders the library the API reports', async ({ page, request }) => {
  const health = await (await request.get('/api/health')).json()
  expect(health.status).toBe('ok')
  expect(health.feeds).toBeGreaterThan(0)
  expect(health.unread).toBeGreaterThan(0)

  await page.goto('/')

  await expect(page).toHaveTitle('Feedelio')
  await expect(page.getByRole('heading', { name: 'Feedelio' })).toBeVisible()

  // The placeholder shell renders Version / Feeds / Unread as a <dl>.
  const values = page.getByRole('definition')
  await expect(values.nth(1)).toHaveText(String(health.feeds))
  await expect(values.nth(2)).toHaveText(String(health.unread))

  // "Cannot reach the API" is rendered with role=alert; its absence is part of
  // the verdict, not a detail.
  await expect(page.getByRole('alert')).toHaveCount(0)
})
