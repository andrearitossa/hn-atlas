import { defineConfig, devices } from '@playwright/test';
import { resolve } from 'node:path';

if (!process.env.SITE_BUNDLE) throw new Error('Set SITE_BUNDLE to the prepared Pages directory.');
const bundle = resolve(process.env.SITE_BUNDLE);
const quote = value => "'" + value.replaceAll("'", "'\\''") + "'";
export default defineConfig({
  testDir: './tests/browser',
  fullyParallel: true,
  retries: 0,
  workers: 2,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:8791',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } },
  ],
  webServer: {
    command: `python3 -m http.server 8791 --bind 127.0.0.1 --directory ${quote(bundle)}`,
    url: 'http://127.0.0.1:8791',
    reuseExistingServer: false,
  },
});
