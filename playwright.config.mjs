import { defineConfig } from '@playwright/test';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
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
      testIgnore: ['accessibility.spec.mjs', 'resize-visibility.spec.mjs', 'live/**'],
    },
    {
      name: 'quality',
      testMatch: ['accessibility.spec.mjs', 'resize-visibility.spec.mjs'],
    },
    {
      name: 'live',
      testDir: 'tests/js/e2e/live',
      testMatch: ['**/*.spec.mjs'],
      globalSetup: join(__dirname, 'tests/js/e2e/live/global-setup.mjs'),
      globalTeardown: join(__dirname, 'tests/js/e2e/live/global-teardown.mjs'),
      timeout: 45_000,
    },
  ],
  webServer: {
    command: `node scripts/serve-static.mjs . ${servePort}`,
    port: servePort,
    reuseExistingServer: !process.env.CI,
    timeout: 10_000,
  },
});
