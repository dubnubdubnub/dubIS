// tests/vitest-global-setup.js
// Verify Python-generated fixtures are up to date.
//
// LOCAL (non-CI): auto-regenerate stale fixtures as a convenience.
// CI: NEVER auto-regenerate — fail loud so that stale COMMITTED fixtures cannot
//     be silently overwritten at test time and pass against fresh fixtures,
//     hiding the drift from review.
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
  const inCI = !!process.env.CI;
  const python = findPython();

  if (!python) {
    // The authoritative fixture-staleness guard in CI lives in .github/workflows/ci.yml
    // (the Python-tier job runs `generate-test-fixtures.py --check`). The JS vitest step
    // may run on a PATH without a discoverable `python`/`python3`, so a missing interpreter
    // here is not in itself a masking hole — defer to the ci.yml guard and skip locally.
    console.log('[vitest-global-setup] Python not found, skipping fixture check.');
    return;
  }

  try {
    execSync(`${python} scripts/generate-test-fixtures.py --check`, {
      stdio: 'pipe',
      timeout: 30_000,
    });
    // Fixtures are up-to-date
  } catch (err) {
    // --check exited non-zero. Usually that means committed fixtures are stale,
    // but the generator can also fail for other reasons (import error, backend bug).
    if (inCI) {
      const detail = String((err && (err.stderr || err.stdout)) || '').trim();
      throw new Error(
        '`generate-test-fixtures.py --check` failed in CI. Either the committed test ' +
          'fixtures are stale (run `python scripts/generate-test-fixtures.py` and commit ' +
          'the result), or the generator itself errored. Refusing to auto-regenerate in ' +
          'CI — that would hide the drift.' +
          (detail ? `\n--- generator output ---\n${detail}` : ''),
      );
    }
    // Local convenience: regenerate so the developer can keep working.
    console.log('[vitest-global-setup] Fixtures stale, regenerating...');
    execSync(`${python} scripts/generate-test-fixtures.py`, {
      stdio: 'inherit',
      timeout: 60_000,
    });
    console.log('[vitest-global-setup] Fixtures regenerated.');
  }
}
