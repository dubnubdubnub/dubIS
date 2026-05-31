// tests/vitest-global-setup.js
// Keep the Python-generated JS test fixtures fresh for LOCAL development.
//
// This is a local-developer convenience ONLY. In CI it is a no-op: the
// authoritative fixture-staleness guard lives in .github/workflows/ci.yml
// (the Python-tier job runs `generate-test-fixtures.py --check` inside the
// project's venv, gated on `run_python || run_js`). The JS CI runner uses a
// bare `python` without the project's Python deps installed, so it cannot run
// the generator here — attempting it would fail for environment reasons, not
// real staleness. So: skip entirely in CI, defer to the ci.yml guard.
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
  // CI: the ci.yml Python-tier step is authoritative; do nothing here.
  if (process.env.CI) return;

  const python = findPython();
  if (!python) {
    console.log('[vitest-global-setup] Python not found, skipping local fixture check.');
    return;
  }

  try {
    execSync(`${python} scripts/generate-test-fixtures.py --check`, {
      stdio: 'pipe',
      timeout: 30_000,
    });
    // Fixtures are up-to-date.
  } catch {
    // Local convenience: regenerate so the developer can keep working.
    // (CI never reaches here — stale committed fixtures fail the ci.yml guard.)
    console.log('[vitest-global-setup] Fixtures stale, regenerating...');
    execSync(`${python} scripts/generate-test-fixtures.py`, {
      stdio: 'inherit',
      timeout: 60_000,
    });
    console.log('[vitest-global-setup] Fixtures regenerated.');
  }
}
