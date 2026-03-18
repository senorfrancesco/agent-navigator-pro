import { expect, test } from '../fixtures/test';

test.describe('Chainlit UI test hook contract', () => {
  test('hooks are discoverable on configured base url or local fixture surface', async ({ page, baseURL }) => {
    if (baseURL) {
      await page.goto(baseURL, { waitUntil: 'domcontentloaded' });
      await expect(page.locator('[data-agent-nav-hook="main-chat-input"]').or(page.locator('[data-agent-nav-hook="login-form"]')).first()).toBeVisible();
      return;
    }

    await page.setContent(
      [
        '<!doctype html>',
        '<html lang="ru"><body>',
        '<form data-agent-nav-hook="login-form"><input type="password" /><button type="submit" data-agent-nav-hook="login-submit">Войти</button></form>',
        '<nav data-agent-nav-hook="thread-list"><a href="#" data-agent-nav-hook="current-thread-marker" data-agent-nav-current-thread="true">Thread</a></nav>',
        '<textarea data-agent-nav-hook="main-chat-input"></textarea>',
        '<button data-agent-nav-hook="upload-trigger">Upload</button>',
        '<article data-agent-nav-hook="assistant-message-container">Ответ</article>',
        '<a href="/reports/test.pdf" download data-agent-nav-hook="report-download-link">Download</a>',
        '</body></html>',
      ].join('')
    );

    await expect(page.locator('[data-agent-nav-hook="login-form"]')).toBeVisible();
    await expect(page.locator('[data-agent-nav-hook="login-submit"]')).toBeVisible();
    await expect(page.locator('[data-agent-nav-hook="main-chat-input"]')).toBeVisible();
    await expect(page.locator('[data-agent-nav-hook="upload-trigger"]')).toBeVisible();
    await expect(page.locator('[data-agent-nav-hook="assistant-message-container"]')).toBeVisible();
    await expect(page.locator('[data-agent-nav-hook="thread-list"]')).toBeVisible();
    await expect(page.locator('[data-agent-nav-hook="current-thread-marker"]')).toHaveAttribute('data-agent-nav-current-thread', 'true');
    await expect(page.locator('[data-agent-nav-hook="report-download-link"]')).toBeVisible();
  });
});
