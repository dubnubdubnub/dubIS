import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: 'test/e2e',
  timeout: 30_000,
  use: {
    browserName: 'chromium',
    screenshot: 'on',
  },
  webServer: {
    command: 'npx serve . -l 3123 -s --no-clipboard',
    port: 3123,
    reuseExistingServer: true,
  },
});
