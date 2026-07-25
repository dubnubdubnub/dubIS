import { defineConfig } from '@playwright/test';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const servePort = parseInt(process.env.SERVE_PORT || '3123', 10);

export default defineConfig({
  testDir: 'tests/js/e2e',
  timeout: 30_000,
  // Absorb transient timing flakes on the self-hosted GUI runners without masking
  // hard failures locally. Local runs get no retries so flakes stay visible.
  retries: process.env.CI ? 2 : 0,
  // Bounded auto-retrying assertion timeout (Playwright has no default expect timeout).
  expect: { timeout: 5000 },
  globalSetup: join(__dirname, 'tests/js/e2e/live/global-setup.mjs'),
  globalTeardown: join(__dirname, 'tests/js/e2e/live/global-teardown.mjs'),
  use: {
    browserName: 'chromium',
    baseURL: `http://localhost:${servePort}`,
    // 'on' forces a screenshot for every test, adding I/O latency on the GUI runners
    // that widens timing windows. Only capture on failure.
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'functional',
      testIgnore: ['accessibility.spec.mjs', 'resize-visibility.spec.mjs', 'live/**'],
    },
    {
      // Windows-only smoke subset, run on the single ephemeral win11 VM
      // (js-windows CI leg). The full functional suite is proven identical on
      // the ubuntu + macos legs, so win11 only re-runs the specs whose behavior
      // can genuinely diverge on Windows: WebView2/Chromium font metrics and
      // sticky/overflow layout, OS-level keyboard & focus, and path/file
      // handling. The full functional suite still runs nightly on win11 via
      // .github/workflows/win11-nightly.yml. Grow this list rather than
      // reverting to --project functional here. See
      // docs/superpowers/specs/2026-07-24-win11-ci-speedup-design.md.
      name: 'windows',
      testMatch: [
        // Layout / render
        // Zoom + panel-collapse: WebView2 font metrics and stacking/overflow behaviour
        // are exactly what these assert, and glyph advances measure differently there
        // (see the sticky-button column widening in css/tokens.css).
        'zoom-sweep.spec.mjs',
        'zoom-sticky-buttons.spec.mjs',
        'zoom-visual.spec.mjs',
        'panel-collapse.spec.mjs',
        'inv-alignment-visual.spec.mjs',
        'sticky-clip-visual.spec.mjs',
        'sticky-buttons.spec.mjs',
        'po-icon-stack-visual.spec.mjs',
        'inv-col-alignment.spec.mjs',
        'inv-col-header.spec.mjs',
        'row-alignment.spec.mjs',
        // OS interaction (keyboard / focus)
        'keyboard-nav.spec.mjs',
        'shortcuts.spec.mjs',
        'search-keyboard.spec.mjs',
        'scroll-keyboard.spec.mjs',
        'modal-focus-trap.spec.mjs',
        // File / path handling
        'import-diff.spec.mjs',
        'po-import.spec.mjs',
        'label-export.spec.mjs',
      ],
    },
    {
      name: 'quality',
      testMatch: ['accessibility.spec.mjs', 'resize-visibility.spec.mjs'],
    },
    {
      name: 'live',
      testDir: 'tests/js/e2e/live',
      testMatch: ['**/*.spec.mjs'],
      timeout: 45_000,
      // The live backend (a real `python -m server` instance, started by
      // tests/js/e2e/live/global-setup.mjs) is one process with module-global
      // mutable state reset via POST /v1/_test/reset in beforeEach. Pin this
      // project to a single, non-parallel worker so isolation no longer depends on CI
      // remembering to pass --workers 1 on the command line.
      workers: 1,
      fullyParallel: false,
    },
  ],
  webServer: {
    command: `node scripts/serve-static.mjs . ${servePort}`,
    port: servePort,
    reuseExistingServer: !process.env.CI,
    timeout: 10_000,
  },
});
