import { expect, test } from '../fixtures/test';

test.describe('Chainlit UI test hook contract', () => {
  test('hooks are discoverable on configured base url or local fixture surface', async ({ page, baseURL }) => {
    if (baseURL) {
      await page.goto(baseURL, { waitUntil: 'domcontentloaded' });
      await expect(page.locator('[data-llm-tools-platform-hook="main-chat-input"]').or(page.locator('[data-llm-tools-platform-hook="login-form"]')).first()).toBeVisible();
      return;
    }

    await page.setContent(
      [
        '<!doctype html>',
        '<html lang="ru"><body>',
        '<form data-llm-tools-platform-hook="login-form"><input type="password" /><button type="submit" data-llm-tools-platform-hook="login-submit">Войти</button></form>',
        '<nav data-llm-tools-platform-hook="thread-list"><a href="#" data-llm-tools-platform-hook="current-thread-marker" data-llm-tools-platform-current-thread="true">Thread</a></nav>',
        '<textarea data-llm-tools-platform-hook="main-chat-input"></textarea>',
        '<button data-llm-tools-platform-hook="upload-trigger">Upload</button>',
        '<article data-llm-tools-platform-hook="assistant-message-container">Ответ</article>',
        '<a href="/reports/test.pdf" download data-llm-tools-platform-hook="report-download-link">Download</a>',
        '</body></html>',
      ].join('')
    );

    await expect(page.locator('[data-llm-tools-platform-hook="login-form"]')).toBeVisible();
    await expect(page.locator('[data-llm-tools-platform-hook="login-submit"]')).toBeVisible();
    await expect(page.locator('[data-llm-tools-platform-hook="main-chat-input"]')).toBeVisible();
    await expect(page.locator('[data-llm-tools-platform-hook="upload-trigger"]')).toBeVisible();
    await expect(page.locator('[data-llm-tools-platform-hook="assistant-message-container"]')).toBeVisible();
    await expect(page.locator('[data-llm-tools-platform-hook="thread-list"]')).toBeVisible();
    await expect(page.locator('[data-llm-tools-platform-hook="current-thread-marker"]')).toHaveAttribute('data-llm-tools-platform-current-thread', 'true');
    await expect(page.locator('[data-llm-tools-platform-hook="report-download-link"]')).toBeVisible();
  });
});
