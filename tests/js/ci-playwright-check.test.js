/**
 * Unit tests for the pure decision logic behind
 * scripts/check-playwright-browsers.mjs (scripts/playwright-health-lib.mjs).
 *
 * That script is CI-only glue, and a mistake in it costs a full CI round trip
 * to discover - on legs that are advisory, and therefore easy to ignore. Two
 * mistakes already happened and are pinned here:
 *
 *  1. An executable-size floor condemned every healthy macOS runner, because
 *     the file Playwright calls the executable is a ~52 KB stub launcher on
 *     macOS by design. The heuristic now looks at the download directory and,
 *     crucially, is diagnostic only - see the tests below.
 *  2. Putting these helpers in the CLI file made a vitest import of it throw
 *     `SyntaxError: Invalid or unexpected token` on the win11 leg only. They
 *     live in a pure, dependency-free module now.
 *
 * The launch probe and the repair are deliberately NOT unit-tested: touching a
 * real browser install is their entire value.
 */
import { describe, it, expect } from 'vitest';
import path from 'node:path';
import {
  MIN_PLAUSIBLE_FILES,
  MIN_PLAUSIBLE_BYTES,
  REVISION_DIR_RE,
  browserDirFor,
  revisionDirsToRemove,
  describeDownload,
  formatBytes,
} from '../../scripts/playwright-health-lib.mjs';

describe('REVISION_DIR_RE', () => {
  it('matches both downloads Playwright needs for headed and headless runs', () => {
    expect(REVISION_DIR_RE.test('chromium-1208')).toBe(true);
    expect(REVISION_DIR_RE.test('chromium_headless_shell-1208')).toBe(true);
  });

  it('does not match neighbouring registry entries or partial names', () => {
    for (const name of ['ffmpeg-1011', 'firefox-1489', 'chromium', 'chromium-', 'chromium-abc']) {
      expect(REVISION_DIR_RE.test(name), name).toBe(false);
    }
  });
});

describe('browserDirFor', () => {
  it('finds the registry root inside a real macOS executable path', () => {
    // Verbatim from the m4-air runner (Playwright 1.58, darwin-arm64).
    const info = browserDirFor(
      '/Users/me/Library/Caches/ms-playwright/chromium-1208/chrome-mac-arm64/'
      + 'Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing',
    );
    expect(info).toEqual({
      registryRoot: ['', 'Users', 'me', 'Library', 'Caches', 'ms-playwright'].join(path.sep),
      name: 'chromium',
      revision: '1208',
      dirName: 'chromium-1208',
    });
  });

  it('finds the registry root inside a Linux executable path', () => {
    const info = browserDirFor('/home/runner/.cache/ms-playwright/chromium-1208/chrome-linux/chrome');
    expect(info?.registryRoot).toBe(['', 'home', 'runner', '.cache', 'ms-playwright'].join(path.sep));
    expect(info?.revision).toBe('1208');
  });

  it('handles Windows backslash paths (the win11 leg)', () => {
    const info = browserDirFor(
      'C:\\Users\\runneradmin\\AppData\\Local\\ms-playwright\\chromium-1208\\chrome-win\\chrome.exe',
    );
    expect(info?.dirName).toBe('chromium-1208');
    expect(info?.registryRoot.endsWith('ms-playwright')).toBe(true);
  });

  it('recognises the headless-shell download too', () => {
    const info = browserDirFor('/c/ms-playwright/chromium_headless_shell-1208/chrome-linux/headless_shell');
    expect(info?.name).toBe('chromium_headless_shell');
  });

  it('returns null when the browser is not a Playwright-managed download', () => {
    // e.g. a `channel: 'chrome'` system install - nothing for us to repair.
    expect(browserDirFor('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')).toBeNull();
    expect(browserDirFor('')).toBeNull();
    expect(browserDirFor(null)).toBeNull();
    // A bare revision dir with no parent would make registryRoot empty, which
    // must never be handed to a recursive delete.
    expect(browserDirFor('chromium-1208/chrome-linux/chrome')).toBeNull();
  });
});

describe('describeDownload', () => {
  it('reports the observed truncated download as truncated', () => {
    // The state that motivated the script: a revision directory left in place
    // by an interrupted download, which `playwright install` then treats as
    // complete forever.
    expect(describeDownload({ exists: true, files: 39, bytes: 428 * 1024 })).toBe('truncated');
  });

  it('reports a real download as complete-looking', () => {
    // Measured on a healthy darwin-arm64 cache.
    expect(describeDownload({ exists: true, files: 335, bytes: 331 * 1024 * 1024 })).toBe('complete-looking');
  });

  it('does NOT judge a healthy macOS install by its 52 KB stub launcher', () => {
    // The regression this replaces: on macOS the named executable is
    // `...app/Contents/MacOS/Google Chrome for Testing`, a ~52 KB launcher, with
    // the real code in Contents/Frameworks. Judging the DIRECTORY keeps that
    // install correctly classified.
    const healthyMacInstall = { exists: true, files: 335, bytes: 331 * 1024 * 1024 };
    expect(describeDownload(healthyMacInstall)).not.toBe('truncated');
  });

  it('reports an absent download as missing', () => {
    expect(describeDownload({ exists: false })).toBe('missing');
    expect(describeDownload(undefined)).toBe('missing');
  });

  it('keeps both floors far from either observed case', () => {
    // Comfortably above the truncated download...
    expect(MIN_PLAUSIBLE_FILES).toBeGreaterThan(39);
    expect(MIN_PLAUSIBLE_BYTES).toBeGreaterThan(428 * 1024);
    // ...and comfortably below a real one, so neither bound is a near miss.
    expect(MIN_PLAUSIBLE_FILES).toBeLessThan(335);
    expect(MIN_PLAUSIBLE_BYTES).toBeLessThan(331 * 1024 * 1024);
  });
});

describe('revisionDirsToRemove', () => {
  it('removes exactly the headed browser and the headless shell for one revision', () => {
    const root = path.join(path.sep, 'cache', 'ms-playwright');
    expect(revisionDirsToRemove({ registryRoot: root, revision: '1208' })).toEqual([
      path.join(root, 'chromium-1208'),
      path.join(root, 'chromium_headless_shell-1208'),
    ]);
  });

  it('never widens to other revisions sharing the registry', () => {
    const dirs = revisionDirsToRemove({ registryRoot: '/cache/ms-playwright', revision: '1208' });
    expect(dirs.every((d) => d.includes('1208'))).toBe(true);
    expect(dirs).toHaveLength(2);
  });
});

describe('formatBytes', () => {
  it('renders the sizes that appear in the failure diagnostic', () => {
    expect(formatBytes(428 * 1024)).toBe('428 KB');
    expect(formatBytes(331 * 1024 * 1024)).toBe('331 MB');
    expect(formatBytes(0)).toBe('0 B');
  });

  it('never throws on a value it could not measure', () => {
    expect(formatBytes(undefined)).toBe('unknown');
    expect(formatBytes(NaN)).toBe('unknown');
  });
});
