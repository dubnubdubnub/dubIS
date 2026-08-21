#!/usr/bin/env node
/**
 * check-playwright-browsers.mjs — prove the browser Playwright will actually
 * launch is present AND usable, and (with --repair) fix it in place.
 *
 * Why this exists
 * ---------------
 * `npx playwright install chromium` decides "already installed" from the mere
 * EXISTENCE of the revision directory (`<registry>/chromium-<rev>/`). A
 * download interrupted partway through — a killed job, a full disk, a
 * `actions/cache` restore that was truncated — leaves that directory in place
 * with a fraction of its files and a stub launcher binary. Every subsequent
 * `playwright install` is then a silent no-op, and the browser stays broken
 * FOREVER on any runner whose browser cache survives between jobs (observed
 * locally: `chromium-1208` with 39 files / 428 KB and a 52 KB stub binary,
 * unrepairable by `npx playwright install chromium`).
 *
 * A leg in that state does not report "browser broken". It reports whatever
 * the specs happen to fail on, which reads like a code defect. This script
 * converts that into one loud, early, unambiguous failure instead — and, with
 * --repair, into a self-healing runner.
 *
 * How it decides
 * --------------
 * The authoritative check is a real `chromium.launch()` + render probe, not a
 * file-existence test: since Playwright 1.49 a headless `chromium.launch()`
 * resolves to the *chromium_headless_shell* download, which is a different
 * directory from the one `executablePath()` names. Only launching exercises
 * the binary the specs will use. The file-size preflight exists purely to turn
 * "browser did not launch" into "the executable is a 52 KB stub", which is a
 * far more actionable log line.
 *
 * Usage:
 *   node scripts/check-playwright-browsers.mjs            # verify, fail loudly
 *   node scripts/check-playwright-browsers.mjs --repair   # verify, self-heal, re-verify
 *
 * Exit 0 = the browser launched and rendered. Exit 1 = it did not, and (in
 * --repair mode) reinstalling from scratch did not fix it.
 */

import { existsSync, statSync, rmSync, appendFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

/**
 * A real Chromium launcher/binary is tens to hundreds of MB on every platform
 * Playwright ships. 1 MiB is far below any legitimate value and far above the
 * observed 52 KB stub, so it separates "truncated download" from "fine" with
 * no risk of a false positive.
 */
export const MIN_EXECUTABLE_BYTES = 1024 * 1024;

/** Playwright's on-disk revision directory names, e.g. `chromium-1208`. */
export const REVISION_DIR_RE = /^(chromium|chromium_headless_shell)-(\d+)$/;

/**
 * Locate the Playwright browser-registry directory that backs an executable
 * path, by finding the `<name>-<revision>` segment inside it.
 *
 * Pure + separator-agnostic (Windows paths mix `\` and `/` in practice), so it
 * is unit-tested directly in tests/js/ci-playwright-check.test.js.
 *
 * @param {string} execPath absolute path to a Playwright-managed executable
 * @returns {{registryRoot: string, name: string, revision: string, dirName: string}|null}
 *   null when the path has no revision segment (i.e. it is not a
 *   Playwright-managed download — e.g. a system Chrome via `channel`).
 */
export function browserDirFor(execPath) {
  if (typeof execPath !== 'string' || execPath === '') return null;
  const parts = execPath.split(/[\\/]/);
  const idx = parts.findIndex((p) => REVISION_DIR_RE.test(p));
  // idx === 0 would mean the registry root is empty — not a real absolute path.
  if (idx <= 0) return null;
  const m = /** @type {RegExpExecArray} */ (REVISION_DIR_RE.exec(parts[idx]));
  return {
    registryRoot: parts.slice(0, idx).join(path.sep) || path.sep,
    name: m[1],
    revision: m[2],
    dirName: parts[idx],
  };
}

/**
 * Classify a Playwright executable from its stat-able facts alone.
 *
 * Pure so the three interesting cases can be asserted without a filesystem.
 *
 * @param {{exists: boolean, size?: number}} facts
 * @returns {'missing'|'stub'|'plausible'}
 */
export function classifyExecutable(facts) {
  if (!facts || !facts.exists) return 'missing';
  if (typeof facts.size !== 'number' || facts.size < MIN_EXECUTABLE_BYTES) return 'stub';
  return 'plausible';
}

/**
 * Every revision directory that must be destroyed to force a genuine
 * re-download. Both the headed browser and the headless shell are removed for
 * the target revision: the launch probe cannot tell us which of the two is
 * corrupt, and reinstalling both is cheap next to a wrong guess.
 *
 * Scoped to the ONE revision Playwright currently wants: removing more than
 * that is pointless (the subsequent `playwright install` garbage-collects
 * unused revisions by itself) and would slow down any other project sharing
 * this box's cache. Removing the directory is nonetheless mandatory — an
 * existing directory is exactly what makes `playwright install` a no-op.
 *
 * @param {{registryRoot: string, revision: string}} info from browserDirFor
 * @returns {string[]} absolute directory paths, in removal order
 */
export function revisionDirsToRemove(info) {
  return ['chromium', 'chromium_headless_shell']
    .map((name) => path.join(info.registryRoot, `${name}-${info.revision}`));
}

// ── IO shell (everything below touches the filesystem, the network, or a
//    browser process, and is exercised by CI rather than by unit tests) ──

/** @param {string} p */
function statFacts(p) {
  if (!existsSync(p)) return { exists: false };
  try {
    return { exists: true, size: statSync(p).size };
  } catch {
    return { exists: false };
  }
}

/**
 * Launch the browser the specs will use and render one trivial page in it.
 * Throws on any failure — the caller turns that into the verdict.
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

/** @returns {string|null} */
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
 * Concurrency: two jobs sharing one machine's browser cache (js-e2e and quality
 * both target m4-air) could repair at the same time. `playwright install` takes
 * a lock on the registry, so the downloads serialise; the removals are
 * idempotent. The only exposure is a job that was mid-launch when another
 * removed the directory — and it reaches this code only when the browser is
 * already unhealthy, so that job was failing regardless. Not worth a lock of
 * our own.
 *
 * @param {{registryRoot: string, revision: string}|null} info
 * @returns {boolean} whether `playwright install` reported success
 */
function repair(info) {
  if (info) {
    for (const dir of revisionDirsToRemove(info)) {
      if (existsSync(dir)) {
        console.log(`  removing corrupt download: ${dir}`);
        rmSync(dir, { recursive: true, force: true });
      } else {
        console.log(`  (not present, nothing to remove: ${dir})`);
      }
    }
  } else {
    console.log('  no Playwright-managed revision directory to remove; reinstalling anyway');
  }
  console.log('  re-running: npx playwright install chromium');
  // One command string rather than (file, args) so `shell: true` doesn't trip
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

async function main() {
  const repairRequested = process.argv.includes('--repair');

  console.log('── Playwright browser health check ──');
  const execPath = await resolveExecutablePath();
  const info = execPath ? browserDirFor(execPath) : null;
  console.log(`  executablePath: ${execPath ?? '(unresolved)'}`);
  if (info) console.log(`  revision dir:   ${info.dirName} (under ${info.registryRoot})`);

  const verdict = execPath ? classifyExecutable(statFacts(execPath)) : 'missing';
  console.log(`  preflight:      ${verdict}`);

  /** @type {string|null} */
  let firstFailure = null;
  /** @type {string|null} */
  let version = null;

  if (verdict === 'plausible') {
    try {
      version = await launchProbe();
    } catch (err) {
      firstFailure = err instanceof Error ? err.message : String(err);
    }
  } else {
    firstFailure = verdict === 'missing'
      ? 'the executable does not exist'
      : `the executable is only ${statFacts(execPath ?? '').size} bytes — a truncated download, not a browser`;
  }

  if (firstFailure && repairRequested) {
    console.log('');
    console.log(`  UNHEALTHY: ${firstFailure}`);
    console.log('  --repair given: forcing a genuine re-download.');
    if (repair(info)) {
      try {
        version = await launchProbe();
        firstFailure = null;
      } catch (err) {
        firstFailure = `still broken after repair: ${err instanceof Error ? err.message : err}`;
      }
    } else {
      firstFailure = `${firstFailure}; and \`npx playwright install chromium\` failed during repair`;
    }
  }

  if (firstFailure) {
    console.log('');
    console.log('::error title=Playwright browser unhealthy::' + firstFailure);
    console.log(`::error::This runner's Playwright browser cache cannot launch a browser. Every E2E failure in this job is an ENVIRONMENT failure, not a code defect — do not debug the specs.`);
    console.log(`::error::executablePath=${execPath ?? '(unresolved)'}${info ? ` revisionDir=${info.registryRoot}${path.sep}${info.dirName}` : ''}`);
    if (!repairRequested) {
      console.log('::error::Re-run this step with --repair to delete the pinned revision and re-download it.');
    }
    appendStepSummary(
      `### ❌ Playwright browser unhealthy\n\n`
      + `\`${execPath ?? '(unresolved)'}\`\n\n`
      + `${firstFailure}\n\n`
      + `E2E results from this job are meaningless.`,
    );
    return 1;
  }

  console.log('');
  console.log(`  OK: launched and rendered — Chromium ${version}`);
  appendStepSummary(`- Playwright browser: healthy (Chromium ${version}, \`${info ? info.dirName : 'unknown revision'}\`)`);
  return 0;
}

// Only run when invoked as a script. The pure helpers above are imported by
// tests/js/ci-playwright-check.test.js, which must not launch a browser.
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = await main();
}
