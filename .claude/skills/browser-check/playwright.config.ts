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
/** run.sh sets these. Reading the config any other way is a mistake worth naming. */
function required(name: string): string {
  const value = process.env[name]
  if (!value) {
    throw new Error(`${name} is not set — run scenarios through run.sh, not playwright directly.`)
  }
  return value
}

const PORT = Number(required('E2E_PORT'))
if (!Number.isInteger(PORT) || PORT <= 0) {
  throw new Error(`E2E_PORT is not a port number: ${process.env.E2E_PORT}`)
}
const ROOT = required('BROWSER_CHECK_ROOT')
const OUTPUT_DIR = required('BROWSER_CHECK_OUTPUT')
const BASE_URL = `http://127.0.0.1:${PORT}`

export default defineConfig({
  testDir: './scenarios',
  outputDir: OUTPUT_DIR,
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
