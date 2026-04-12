import { defineConfig } from '@playwright/test';

const servePort = parseInt(process.env.SERVE_PORT || '3123', 10);

export default defineConfig({
  testDir: 'tests/js/e2e',
  timeout: 30_000,
  use: {
    browserName: 'chromium',
    baseURL: `http://localhost:${servePort}`,
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
    command: `node scripts/serve-static.mjs . ${servePort}`,
    port: servePort,
    reuseExistingServer: !process.env.CI,
    timeout: 10_000,
  },
});
