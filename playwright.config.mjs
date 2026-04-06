import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: 'tests/js/e2e',
  timeout: 30_000,
  use: {
    browserName: 'chromium',
    screenshot: 'on',
  },
  projects: [
    {
      name: 'functional',
      testIgnore: ['accessibility.spec.mjs', 'resize-visibility.spec.mjs'],
    },
    {
      name: 'quality',
      testMatch: ['accessibility.spec.mjs', 'resize-visibility.spec.mjs'],
    },
  ],
  webServer: {
    command: 'npx serve . -l 3123 -s --no-clipboard',
    port: 3123,
    reuseExistingServer: true,
  },
});
