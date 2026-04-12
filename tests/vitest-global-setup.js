// tests/vitest-global-setup.js
// Auto-regenerate Python-generated fixtures if stale.
import { execSync } from 'node:child_process';

export async function setup() {
  try {
    execSync('python scripts/generate-test-fixtures.py --check', {
      stdio: 'pipe',
      timeout: 30_000,
    });
    // Fixtures are up-to-date
  } catch {
    // --check failed (exit code 1) → fixtures are stale, regenerate
    console.log('[vitest-global-setup] Fixtures stale, regenerating...');
    try {
      execSync('python scripts/generate-test-fixtures.py', {
        stdio: 'inherit',
        timeout: 60_000,
      });
      console.log('[vitest-global-setup] Fixtures regenerated.');
    } catch (e) {
      throw new Error(
        'Failed to regenerate test fixtures. Is Python available?\n' + e.message
      );
    }
  }
}
