import { SmokePage } from '../pages/smoke-page';
import { expect, test } from '../fixtures/test';

test.describe('E2E smoke harness', () => {
  test('loads configured base url or isolated local smoke surface', async ({ page, baseURL, openSmokeSurface }) => {
    await openSmokeSurface();

    const smokePage = new SmokePage(page);

    if (baseURL) {
      await expect(page).toHaveURL(new RegExp('^' + baseURL.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
      await expect(page).toHaveTitle(/.+/);
      return;
    }

    await expect(smokePage.title()).toHaveText('Smoke Harness');
    await expect(smokePage.mode()).toHaveText('local-static');
    await expect(smokePage.readyButton()).toBeVisible();
  });
});
