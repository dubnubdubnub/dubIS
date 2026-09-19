import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    globalSetup: ['./tests/vitest-global-setup.js'],
    projects: [
      {
        test: {
          name: 'core',
          include: ['tests/js/**/*.test.js', 'tests/js/**/*.test.mjs'],
          exclude: [
            'tests/js/contrast.test.js',
            'tests/js/style-audit.test.js',
            'tests/js/ui-helpers.test.js',
            // Property tests are their own project below: they need the
            // fast-check global config (seed, run count) that `property`'s
            // setupFiles installs, and their run budget is tuned separately.
            'tests/js/property/**',
          ],
        },
      },
      {
        // Property-based tests (docs/property-testing.md). Separate from `core`
        // only because they carry a global configuration -- the same suite of
        // pure-function unit tests otherwise, run by verify.sh and CI's js leg
        // alongside core, never instead of it.
        test: {
          name: 'property',
          include: ['tests/js/property/**/*.property.test.mjs'],
          setupFiles: ['./tests/js/property/setup.mjs'],
        },
      },
      {
        test: {
          name: 'quality',
          include: [
            'tests/js/contrast.test.js',
            'tests/js/style-audit.test.js',
            'tests/js/ui-helpers.test.js',
          ],
        },
      },
    ],
  },
});
