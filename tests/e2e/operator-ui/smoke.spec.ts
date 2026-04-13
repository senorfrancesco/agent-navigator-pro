import { expect, test } from '@playwright/test';

function resolveOperatorUiUrl(baseURL: string | undefined): string | null {
  if (!baseURL) {
    return null;
  }

  if (baseURL.endsWith('/operator-ui') || baseURL.endsWith('/operator-ui/')) {
    return baseURL;
  }

  return new URL('/operator-ui/', baseURL).toString();
}

test.describe('Operator UI smoke', () => {
  test('loads backend-served operator shell and core workspaces', async ({ page, baseURL }) => {
    const operatorUrl = resolveOperatorUiUrl(baseURL);

    test.skip(!operatorUrl, 'BASE_URL is required for backend-served operator UI smoke');

    await page.goto(operatorUrl!, { waitUntil: 'domcontentloaded' });

    await expect(page.locator('[data-llm-tools-platform-hook="operator-topbar-start"]')).toBeVisible();
    await expect(page.locator('[data-llm-tools-platform-hook="operator-topbar-runtime-badge"]')).toBeVisible();
    await expect(page.locator('[data-llm-tools-platform-hook="operator-section-overview"]')).toBeVisible();
    await expect(page.locator('[data-llm-tools-platform-hook="operator-overview-metrics"]')).toBeVisible();

    await page.locator('[data-llm-tools-platform-hook="operator-nav-services"]').click();
    await expect(page.locator('[data-llm-tools-platform-hook="operator-section-services"]')).toBeVisible();
    await expect(page.locator('[data-llm-tools-platform-hook="operator-services-strip"]')).toBeVisible();
    await expect(page.locator('[data-llm-tools-platform-hook="operator-services-grid"]')).toBeVisible();
    await expect(page.locator('[data-llm-tools-platform-hook="operator-services-metrics"]')).toBeVisible();
    await expect(page.locator('[data-llm-tools-platform-hook="operator-services-links"]')).toBeVisible();
    await expect(page.locator('[data-llm-tools-platform-hook="operator-services-logs"]')).toBeVisible();

    await page.locator('[data-llm-tools-platform-hook="operator-nav-deploy"]').click();
    await expect(page.locator('[data-llm-tools-platform-hook="operator-section-deploy"]')).toBeVisible();
    await expect(page.locator('[data-llm-tools-platform-hook="operator-deploy-strip"]')).toBeVisible();
    await expect(page.locator('[data-llm-tools-platform-hook="operator-deploy-summary"]')).toBeVisible();
    await expect(page.locator('[data-llm-tools-platform-hook="operator-deploy-metrics"]')).toBeVisible();
    await expect(page.locator('[data-llm-tools-platform-hook="operator-deploy-links"]')).toBeVisible();
    await expect(page.locator('[data-llm-tools-platform-hook="operator-deploy-stepper"]')).toBeVisible();
    await expect(page.locator('[data-llm-tools-platform-hook="operator-deploy-logs"]')).toBeVisible();

    await page.locator('[data-llm-tools-platform-hook="operator-nav-config"]').click();
    await expect(page.locator('[data-llm-tools-platform-hook="operator-section-config"]')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Применить изменения' })).toBeVisible();

    await page.getByRole('button', { name: 'Реестр моделей и пути' }).click();
    await expect(page.getByRole('button', { name: /Выбрать (файл|папку)/ }).first()).toBeVisible();

    await page.getByRole('button', { name: 'Офлайн-бандл / Контейнеры' }).click();
    await page.getByRole('button', { name: 'Артефакты и пути' }).click();
    await expect(page.getByRole('button', { name: /Выбрать (файл|папку)/ }).first()).toBeVisible();

    await expect(page.locator('[data-llm-tools-platform-hook="operator-help-open"]')).toBeVisible();
    await page.locator('[data-llm-tools-platform-hook="operator-help-open"]').click();
    await expect(page.locator('[data-llm-tools-platform-hook="operator-help-drawer"]')).toBeVisible();
    await page.locator('[data-llm-tools-platform-hook="operator-help-close"]').click();

    await page.locator('[data-llm-tools-platform-hook="operator-nav-launch"]').click();
    await expect(page.locator('[data-llm-tools-platform-hook="operator-launch-runtime-state"]').first()).toBeVisible();
  });
});
