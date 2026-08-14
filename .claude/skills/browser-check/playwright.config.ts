import { defineConfig, devices } from '@playwright/test'

/**
 * Config for exploratory scenario runs. `run.sh` copies this next to a scratch
 * `scenarios/` directory outside the repo and fills in the environment.
 *
 * Deliberately different from `e2e/playwright.config.ts`:
 *  - one worker, no parallelism: scenarios share one SQLite library and set
 *    each other's state up, so interleaving them would produce lies;
 *  - `reuseExistingServer: false`: the port is freshly chosen, so anything
 *    already listening on it is a bug we want to see, not a server to adopt;
 *  - screenshot and trace on failure, because a failing verdict has to come
 *    with evidence.
 */
const PORT = Number(process.env.E2E_PORT)
const ROOT = process.env.BROWSER_CHECK_ROOT!
const BASE_URL = `http://127.0.0.1:${PORT}`

export default defineConfig({
  testDir: './scenarios',
  outputDir: process.env.BROWSER_CHECK_OUTPUT!,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  timeout: 30_000,
  use: {
    baseURL: BASE_URL,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `${ROOT}/e2e/stack.sh`,
    cwd: `${ROOT}/e2e`,
    url: `${BASE_URL}/api/health`,
    reuseExistingServer: false,
    timeout: 180_000,
    stdout: 'pipe',
    stderr: 'pipe',
  },
})
