#!/usr/bin/env node
/**
 * check-playwright-browsers.mjs - prove the browser Playwright will actually
 * launch is usable, and (with --repair) fix it in place.
 *
 * Why this exists
 * ---------------
 * `npx playwright install chromium` decides "already installed" from the mere
 * EXISTENCE of the revision directory (`<registry>/chromium-<rev>/`). A
 * download interrupted partway through - a killed job, a full disk, an
 * `actions/cache` restore that was truncated - leaves that directory in place
 * with a fraction of its files, and every subsequent `playwright install` is a
 * silent no-op. On a runner whose browser cache survives between jobs, the
 * browser then stays broken forever.
 *
 * A leg in that state does not report "browser broken". It reports whatever the
 * specs happen to fail on, which reads like a code defect. This turns it into
 * one loud, early, unambiguous failure instead - and, with --repair, into a
 * self-healing runner.
 *
 * How it decides
 * --------------
 * The verdict is a real `chromium.launch()` plus a render, and NOTHING ELSE.
 * Two reasons:
 *
 *  - Only launching exercises the binary the specs will use. Since Playwright
 *    1.49 a headless `chromium.launch()` resolves to the separate
 *    `chromium_headless_shell` download, which lives in a different directory
 *    from the one `executablePath()` names.
 *  - File-size heuristics on the executable are actively wrong. On macOS the
 *    named executable is a ~52 KB stub launcher by design (the code is in the
 *    .app's Frameworks directory), so a size floor condemns every healthy macOS
 *    runner. The first version of this script did exactly that and deleted a
 *    working browser cache on the m4-air to "repair" it.
 *
 * The download inventory (file count / total bytes of the revision directory)
 * is reported alongside a failure as a diagnostic only - never as a verdict.
 *
 * Usage:
 *   node scripts/check-playwright-browsers.mjs            # verify, fail loudly
 *   node scripts/check-playwright-browsers.mjs --repair   # verify, self-heal, re-verify
 *
 * Exit 0 = the browser launched and rendered. Exit 1 = it did not, and (in
 * --repair mode) a from-scratch reinstall did not fix it.
 */

import { existsSync, statSync, rmSync, readdirSync, appendFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import {
  browserDirFor,
  revisionDirsToRemove,
  describeDownload,
  formatBytes,
} from './playwright-health-lib.mjs';

/** Recursive file count + byte total, for the failure diagnostic. */
function inventoryOf(dir) {
  if (!dir || !existsSync(dir)) return { exists: false };
  let files = 0;
  let bytes = 0;
  /** @param {string} d */
  const walk = (d) => {
    let entries;
    try {
      entries = readdirSync(d, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      const full = path.join(d, entry.name);
      if (entry.isDirectory()) {
        walk(full);
      } else if (entry.isFile()) {
        files += 1;
        try {
          bytes += statSync(full).size;
        } catch {
          // A file that vanished mid-walk tells us nothing; keep counting.
        }
      }
    }
  };
  walk(dir);
  return { exists: true, files, bytes };
}

/**
 * Launch the browser the specs will use and render one trivial page in it.
 * Throws on any failure - the caller turns that into the verdict.
 * @returns {Promise<string>} the browser's reported version
 */
async function launchProbe() {
  const { chromium } = await import('@playwright/test');
  const browser = await chromium.launch({ timeout: 120_000 });
  try {
    const version = browser.version();
    const page = await browser.newPage();
    await page.setContent('<h1 id="probe">ok</h1>');
    const text = await page.textContent('#probe', { timeout: 15_000 });
    if (text !== 'ok') {
      throw new Error(`render probe returned ${JSON.stringify(text)}, expected "ok"`);
    }
    return version;
  } finally {
    await browser.close();
  }
}

/** @param {number} ms */
function delay(ms) {
  return new Promise((resolve) => { setTimeout(resolve, ms); });
}

/**
 * Probe, and retry once after a pause before believing a failure.
 *
 * Several jobs share one machine's browser cache (js-e2e and quality both
 * target m4-air), so a launch can lose a transient fight over a temp profile or
 * a process kill. One retry costs a couple of seconds and keeps a shared-box
 * hiccup from being reported as a corrupt install.
 *
 * @returns {Promise<{version: string|null, error: string|null}>}
 */
async function probeWithRetry() {
  try {
    return { version: await launchProbe(), error: null };
  } catch (first) {
    const firstMsg = first instanceof Error ? first.message : String(first);
    console.log(`  launch attempt 1 failed: ${firstMsg}`);
    console.log('  retrying once in 3s (a shared runner can lose a transient race)');
    await delay(3000);
    try {
      return { version: await launchProbe(), error: null };
    } catch (second) {
      return { version: null, error: second instanceof Error ? second.message : String(second) };
    }
  }
}

/** @returns {Promise<string|null>} */
async function resolveExecutablePath() {
  try {
    const { chromium } = await import('@playwright/test');
    return chromium.executablePath();
  } catch (err) {
    console.log(`  could not resolve an executable path: ${err instanceof Error ? err.message : err}`);
    return null;
  }
}

/**
 * Delete the pinned revision's downloads and reinstall them.
 *
 * Concurrency: `playwright install` locks the registry, so concurrent downloads
 * serialise, and the removals are idempotent. The residual exposure is a job
 * mid-launch when another removes the directory - but this code only runs after
 * a launch has already failed twice, so that job was failing anyway. Not worth
 * a lock of our own.
 *
 * @param {{registryRoot: string, revision: string}|null} info
 * @returns {boolean} whether `playwright install` reported success
 */
function repair(info) {
  if (info) {
    for (const dir of revisionDirsToRemove(info)) {
      if (existsSync(dir)) {
        console.log(`  removing: ${dir}`);
        rmSync(dir, { recursive: true, force: true });
      } else {
        console.log(`  (not present, nothing to remove: ${dir})`);
      }
    }
  } else {
    console.log('  no Playwright-managed revision directory to remove; reinstalling anyway');
  }
  console.log('  re-running: npx playwright install chromium');
  // One command string rather than (file, args) so `shell: true` does not trip
  // node's DEP0190 unescaped-args warning. shell: true is what makes `npx`
  // resolve identically from bash and from PowerShell (the win11 leg runs its
  // steps in PowerShell). No interpolation here, so nothing to escape.
  const res = spawnSync('npx playwright install chromium', {
    stdio: 'inherit',
    shell: true,
  });
  return res.status === 0;
}

/** @param {string} markdown */
function appendStepSummary(markdown) {
  const file = process.env.GITHUB_STEP_SUMMARY;
  if (!file) return;
  try {
    appendFileSync(file, `${markdown}\n`);
  } catch {
    // A summary is a nicety; never fail the check over it.
  }
}

/** @param {string|null} execPath */
function reportDownload(execPath) {
  const info = execPath ? browserDirFor(execPath) : null;
  if (!info) return null;
  const inv = inventoryOf(path.join(info.registryRoot, info.dirName));
  const verdict = describeDownload(inv);
  console.log(
    `  download:       ${verdict}`
    + (inv.exists ? ` (${inv.files} files, ${formatBytes(inv.bytes)})` : ''),
  );
  return { info, inv, verdict };
}

async function main() {
  const repairRequested = process.argv.includes('--repair');

  console.log('-- Playwright browser health check --');
  const execPath = await resolveExecutablePath();
  const info = execPath ? browserDirFor(execPath) : null;
  console.log(`  executablePath: ${execPath ?? '(unresolved)'}`);
  if (info) console.log(`  revision dir:   ${info.dirName} (under ${info.registryRoot})`);

  let { version, error } = await probeWithRetry();

  if (error && repairRequested) {
    console.log('');
    console.log(`  UNHEALTHY: ${error}`);
    // Only interesting once something is actually wrong.
    reportDownload(execPath);
    console.log('  --repair given: forcing a genuine re-download.');
    if (repair(info)) {
      const retry = await probeWithRetry();
      version = retry.version;
      error = retry.error ? `still broken after repair: ${retry.error}` : null;
    } else {
      error = `${error}; and \`npx playwright install chromium\` failed during repair`;
    }
  }

  if (error) {
    console.log('');
    if (!repairRequested) reportDownload(execPath);
    console.log(`::error title=Playwright browser unhealthy::${error}`);
    console.log("::error::This runner's Playwright browser cache cannot launch a browser. Every E2E failure in this job is an ENVIRONMENT failure, not a code defect - do not debug the specs.");
    console.log(`::error::executablePath=${execPath ?? '(unresolved)'}${info ? ` revisionDir=${path.join(info.registryRoot, info.dirName)}` : ''}`);
    if (!repairRequested) {
      console.log('::error::Re-run this step with --repair to delete the pinned revision and re-download it.');
    }
    appendStepSummary(
      '### Playwright browser unhealthy\n\n'
      + `\`${execPath ?? '(unresolved)'}\`\n\n`
      + `${error}\n\n`
      + 'E2E results from this job are meaningless.',
    );
    return 1;
  }

  console.log('');
  console.log(`  OK: launched and rendered - Chromium ${version}`);
  appendStepSummary(`- Playwright browser: healthy (Chromium ${version}, \`${info ? info.dirName : 'unknown revision'}\`)`);
  return 0;
}

// Only run when invoked as a script. No top-level await: this file must stay
// parseable by every loader that might touch it.
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().then(
    (code) => { process.exitCode = code; },
    (err) => {
      console.log(`::error title=Playwright browser check crashed::${err instanceof Error ? err.stack : err}`);
      process.exitCode = 1;
    },
  );
}
