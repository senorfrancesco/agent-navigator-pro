import type { Page } from '@playwright/test';

export class SmokePage {
  constructor(private readonly page: Page) {}

  title() {
    return this.page.getByTestId('smoke-title');
  }

  mode() {
    return this.page.getByTestId('smoke-mode');
  }

  readyButton() {
    return this.page.getByRole('button', { name: 'Ready' });
  }
}
