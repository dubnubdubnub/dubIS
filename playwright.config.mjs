import { defineConfig } from '@playwright/test';

const servePort = parseInt(process.env.SERVE_PORT || '3123', 10);

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
    command: `npx serve . -l ${servePort} -s --no-clipboard`,
    port: servePort,
    reuseExistingServer: true,
  },
});
