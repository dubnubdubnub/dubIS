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
import { existsSync } from 'node:fs';

// The project's OWN interpreter first, then whatever is on PATH.
//
// A bare `python3` is often a system interpreter without the project's
// dependencies (macOS ships one at /usr/bin/python3), and the generator needs
// xlrd to convert the .xls fixtures. It now refuses to write an empty fixture
// when it cannot convert anything — but the right answer is to not reach for
// the wrong interpreter in the first place, which is also what
// scripts/verify.sh does via $PYTHON.
function findPython() {
  const candidates = [
    process.env.DUBIS_PYTHON,
    process.env.PYTHON,
    '.venv/bin/python',
    '.venv/Scripts/python.exe',
  ].filter(Boolean);
  for (const cmd of candidates) {
    if (cmd.includes('/') || cmd.includes('\\')) {
      if (existsSync(cmd)) return cmd;
      continue;
    }
    try {
      execSync(`${cmd} --version`, { stdio: 'pipe' });
      return cmd;
    } catch {
      // not found, try next
    }
  }
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
    console.log(`[vitest-global-setup] Fixtures stale, regenerating with ${python}...`);
    try {
      execSync(`${python} scripts/generate-test-fixtures.py`, {
        stdio: 'inherit',
        timeout: 60_000,
      });
      console.log('[vitest-global-setup] Fixtures regenerated.');
    } catch {
      // A convenience that cannot run must not look like a fixture problem:
      // say the regeneration failed and leave the committed fixtures alone.
      console.log(
        `[vitest-global-setup] Could not regenerate fixtures with ${python} — `
        + 'committed fixtures left untouched. If JS tests fail on fixture values, '
        + 'run `python scripts/generate-test-fixtures.py` under the project venv.',
      );
    }
  }
}
