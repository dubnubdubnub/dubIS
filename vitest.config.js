import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    globalSetup: ['./tests/vitest-global-setup.js'],
    projects: [
      {
        test: {
          name: 'core',
          include: ['tests/js/**/*.test.js'],
          exclude: [
            'tests/js/contrast.test.js',
            'tests/js/style-audit.test.js',
            'tests/js/ui-helpers.test.js',
          ],
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
