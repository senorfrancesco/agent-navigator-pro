import { test as base, expect } from '@playwright/test';

type SmokeFixture = {
  openSmokeSurface: () => Promise<void>;
};

export const test = base.extend<SmokeFixture>({
  openSmokeSurface: async ({ page, baseURL }, use) => {
    await use(async () => {
      if (baseURL) {
        await page.goto(baseURL, { waitUntil: 'domcontentloaded' });
        return;
      }

      await page.setContent(
        [
          '<!doctype html>',
          '<html lang="ru">',
          '<head>',
          '  <meta charset="UTF-8" />',
          '  <title>Playwright Smoke Surface</title>',
          '</head>',
          '<body>',
          '  <main>',
          '    <h1 data-testid="smoke-title">Smoke Harness</h1>',
          '    <p data-testid="smoke-mode">local-static</p>',
          '    <button type="button">Ready</button>',
          '  </main>',
          '</body>',
          '</html>',
        ].join('')
      );
    });
  },
});

export { expect };
