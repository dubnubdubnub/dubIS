/**
 * Unit tests for the pure decision logic in
 * scripts/check-playwright-browsers.mjs.
 *
 * That script is CI-only shell glue, and a typo in it costs a full CI round
 * trip to discover — on the one leg it exists to protect, which is advisory and
 * therefore easy to ignore. The path parsing and the size threshold are the two
 * parts that can be wrong without failing loudly, so they get tested here where
 * a mistake is caught by `npx vitest run --project core`.
 *
 * The launch probe and the repair are deliberately NOT unit-tested: they are
 * the parts whose whole value is touching a real browser install.
 */
import { describe, it, expect } from 'vitest';
import path from 'node:path';
import {
  MIN_EXECUTABLE_BYTES,
  REVISION_DIR_RE,
  browserDirFor,
  classifyExecutable,
  revisionDirsToRemove,
} from '../../scripts/check-playwright-browsers.mjs';

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
    // Verbatim from this repo's Playwright 1.58 on darwin-arm64.
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
    // e.g. a `channel: 'chrome'` system install — nothing for us to repair.
    expect(browserDirFor('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')).toBeNull();
    expect(browserDirFor('')).toBeNull();
    expect(browserDirFor(null)).toBeNull();
    // A bare revision dir with no parent would make registryRoot empty, which
    // must never be handed to a recursive delete.
    expect(browserDirFor('chromium-1208/chrome-linux/chrome')).toBeNull();
  });
});

describe('classifyExecutable', () => {
  it('calls a missing file missing', () => {
    expect(classifyExecutable({ exists: false })).toBe('missing');
    expect(classifyExecutable(undefined)).toBe('missing');
  });

  it('calls the observed 52 KB truncated download a stub', () => {
    // The size actually found on a machine where `playwright install` refused
    // to repair itself, and the reason this script exists.
    expect(classifyExecutable({ exists: true, size: 52064 })).toBe('stub');
  });

  it('treats a real browser binary as plausible', () => {
    expect(classifyExecutable({ exists: true, size: 170 * 1024 * 1024 })).toBe('plausible');
  });

  it('puts the threshold well clear of both cases', () => {
    expect(classifyExecutable({ exists: true, size: MIN_EXECUTABLE_BYTES - 1 })).toBe('stub');
    expect(classifyExecutable({ exists: true, size: MIN_EXECUTABLE_BYTES })).toBe('plausible');
    // Far above any truncated download, far below any real one.
    expect(MIN_EXECUTABLE_BYTES).toBeGreaterThan(52064 * 4);
    expect(MIN_EXECUTABLE_BYTES).toBeLessThan(10 * 1024 * 1024);
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
