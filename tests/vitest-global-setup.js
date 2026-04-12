// tests/vitest-global-setup.js
// Auto-regenerate Python-generated fixtures if stale.
// Gracefully skips if Python is not available (e.g., JS-only CI runners).
import { execSync } from 'node:child_process';

function findPython() {
  for (const cmd of ['python', 'python3']) {
    try {
      execSync(`${cmd} --version`, { stdio: 'pipe' });
      return cmd;
    } catch {
      // not found, try next
    }
  }
  return null;
}

export async function setup() {
  const python = findPython();
  if (!python) {
    console.log('[vitest-global-setup] Python not found, skipping fixture check.');
    return;
  }
  try {
    execSync(`${python} scripts/generate-test-fixtures.py --check`, {
      stdio: 'pipe',
      timeout: 30_000,
    });
    // Fixtures are up-to-date
  } catch {
    // --check failed (exit code 1) → fixtures are stale, regenerate
    console.log('[vitest-global-setup] Fixtures stale, regenerating...');
    execSync(`${python} scripts/generate-test-fixtures.py`, {
      stdio: 'inherit',
      timeout: 60_000,
    });
    console.log('[vitest-global-setup] Fixtures regenerated.');
  }
}
