/**
 * playwright-health-lib.mjs - pure helpers for check-playwright-browsers.mjs.
 *
 * Split out from the CLI so the unit tests import nothing that touches the
 * filesystem, spawns a process, reads process.argv, or awaits at the top level.
 * (The first version put these in the CLI file; importing it from a vitest test
 * parsed fine on macOS/Linux and threw `SyntaxError: Invalid or unexpected
 * token` on the win11 leg. A pure, dependency-free module removes the whole
 * question.)
 *
 * ASCII only, deliberately: this is loaded by three different toolchains
 * (node directly, vitest/vite, and PowerShell-launched node on Windows).
 */

import path from 'node:path';

/** Playwright's on-disk revision directory names, e.g. `chromium-1208`. */
export const REVISION_DIR_RE = /^(chromium|chromium_headless_shell)-(\d+)$/;

/**
 * A complete Chromium download is a few hundred files and well over 100 MB on
 * every platform Playwright ships. These floors sit far below any real download
 * and far above the truncated one that motivated this script (39 files /
 * 428 KB), so the two are trivially separable.
 *
 * DIAGNOSTIC ONLY. Nothing decides the browser is broken from these numbers --
 * see describeDownload.
 */
export const MIN_PLAUSIBLE_FILES = 100;
export const MIN_PLAUSIBLE_BYTES = 50 * 1024 * 1024;

/**
 * Locate the Playwright browser-registry directory that backs an executable
 * path, by finding the `<name>-<revision>` segment inside it.
 *
 * Separator-agnostic: Windows paths mix `\` and `/` in practice.
 *
 * @param {string|null|undefined} execPath path to a Playwright-managed executable
 * @returns {{registryRoot: string, name: string, revision: string, dirName: string}|null}
 *   null when the path has no revision segment, i.e. it is not a
 *   Playwright-managed download (e.g. a system Chrome selected via `channel`).
 */
export function browserDirFor(execPath) {
  if (typeof execPath !== 'string' || execPath === '') return null;
  const parts = execPath.split(/[\\/]/);
  const idx = parts.findIndex((p) => REVISION_DIR_RE.test(p));
  // idx === 0 would leave the registry root empty, which must never reach a
  // recursive delete.
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
 * Every revision directory that must be destroyed to force a genuine
 * re-download. Both the headed browser and the headless shell go, for the
 * target revision: a failed launch cannot tell us which of the two is corrupt,
 * and reinstalling both is cheap next to a wrong guess.
 *
 * Scoped to the ONE revision Playwright currently wants. Removing more is
 * pointless (the subsequent `playwright install` garbage-collects unused
 * revisions itself) and would slow down anything else sharing the box's cache.
 * Removing the directory at all is nonetheless mandatory: an existing directory
 * is exactly what makes `playwright install` a no-op.
 *
 * @param {{registryRoot: string, revision: string}} info from browserDirFor
 * @returns {string[]} absolute directory paths, in removal order
 */
export function revisionDirsToRemove(info) {
  return ['chromium', 'chromium_headless_shell']
    .map((name) => path.join(info.registryRoot, `${name}-${info.revision}`));
}

/**
 * Describe a browser download from its directory inventory, for the log only.
 *
 * Why the *directory* and not the executable: on macOS the file Playwright
 * names as the executable is
 * `Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing`,
 * which is a 52 KB stub launcher by design -- the real code lives in
 * `Contents/Frameworks/...Framework.framework`. A perfectly healthy install
 * therefore has a 52 KB "executable", so an executable-size threshold flags
 * every healthy macOS runner as broken. (It did: the first version of this
 * script deleted and re-downloaded a working browser cache on the m4-air.)
 *
 * Hence: this function never decides anything. The verdict is a real
 * `chromium.launch()`. This only turns "the browser did not launch" into "and
 * by the way, its download is 39 files / 428 KB", which is what makes the log
 * actionable.
 *
 * @param {{exists: boolean, files?: number, bytes?: number}} inventory
 * @returns {'missing'|'truncated'|'complete-looking'}
 */
export function describeDownload(inventory) {
  if (!inventory || !inventory.exists) return 'missing';
  const files = typeof inventory.files === 'number' ? inventory.files : 0;
  const bytes = typeof inventory.bytes === 'number' ? inventory.bytes : 0;
  if (files < MIN_PLAUSIBLE_FILES || bytes < MIN_PLAUSIBLE_BYTES) return 'truncated';
  return 'complete-looking';
}

/** Human-readable byte count for log lines. */
export function formatBytes(bytes) {
  if (typeof bytes !== 'number' || !Number.isFinite(bytes) || bytes < 0) return 'unknown';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 && unit > 0 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}
