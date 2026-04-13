import { defineConfig, devices } from '@playwright/test';

const baseURL = process.env.BASE_URL?.trim() || undefined;
const liveMode = process.env.OPENWEBUI_LIVE_MODE?.trim() === '1';
const slowMo = liveMode ? 250 : 0;

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30_000,
  expect: {
    timeout: 5_000,
  },
  fullyParallel: !liveMode,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: liveMode ? 1 : undefined,
  reporter: [['list']],
  use: {
    baseURL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    headless: !liveMode,
    launchOptions: slowMo > 0 ? { slowMo } : undefined,
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        browserName: 'chromium',
      },
    },
  ],
});
