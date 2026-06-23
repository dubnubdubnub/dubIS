// @vitest-environment jsdom
/**
 * command-palette.test.js — TDD tests for js/components/command-palette.js
 *
 * Tests cover:
 *   - fuzzyScore: subsequence matching, contiguity bonus, word-boundary bonus, prefix bonus, case-insensitive, no-match returns 0
 *   - rankCommands: filter+sort by score, empty query returns original order, keyword matching
 *   - CommandPalette DOM: open populates list, typing filters, Up/Down navigates, Enter runs command,
 *     Esc closes, run() error is surfaced via AppLog.error
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Mocks ────────────────────────────────────────────────────────────────────

vi.mock('../../js/a11y/focus-trap.js', () => ({
  trap: vi.fn(),
  release: vi.fn(),
}));

vi.mock('../../js/api.js', () => ({
  api: vi.fn(async () => ({})),
  AppLog: { warn: vi.fn(), error: vi.fn(), info: vi.fn() },
  whenPywebviewReady: vi.fn(async () => {}),
}));

vi.mock('../../js/constants.js', () => ({
  SECTION_ORDER: [],
  FIELDNAMES: [],
}));

vi.mock('../../js/store.js', () => ({
  store: { preferences: { shortcuts: { redo: 'both' } } },
  getShortcutPrefs: () => ({ redo: 'both' }),
}));

// ── Imports under test ───────────────────────────────────────────────────────

import { fuzzyScore, rankCommands, CommandPalette } from '../../js/components/command-palette.js';
import { trap as trapMock, release as releaseMock } from '../../js/a11y/focus-trap.js';
import { AppLog as AppLogMock } from '../../js/api.js';

// ── fuzzyScore tests ─────────────────────────────────────────────────────────

describe('fuzzyScore', () => {
  it('returns 0 when query has no subsequence match', () => {
    expect(fuzzyScore('xyz', 'Open Preferences')).toBe(0);
  });

  it('returns 0 for empty text', () => {
    expect(fuzzyScore('op', '')).toBe(0);
  });

  it('returns positive score for a subsequence match', () => {
    expect(fuzzyScore('op', 'Open Preferences')).toBeGreaterThan(0);
  });

  it('is case-insensitive', () => {
    const lower = fuzzyScore('op', 'open prefs');
    const upper = fuzzyScore('OP', 'open prefs');
    expect(lower).toBeGreaterThan(0);
    expect(upper).toBeGreaterThan(0);
    expect(lower).toBe(upper);
  });

  it('contiguous match scores higher than scattered', () => {
    // 'pre' appears contiguously in 'Preferences', scattered in 'Print Enabled'
    const contiguous = fuzzyScore('pre', 'Preferences');
    const scattered  = fuzzyScore('pre', 'Print Enabled');
    expect(contiguous).toBeGreaterThan(scattered);
  });

  it('prefix match scores higher than mid-word match', () => {
    const prefix  = fuzzyScore('op', 'Open Something');
    const midWord = fuzzyScore('op', 'Stop Opening');
    expect(prefix).toBeGreaterThan(midWord);
  });

  it('full exact match scores higher than partial match', () => {
    const exact   = fuzzyScore('preferences', 'Preferences');
    const partial = fuzzyScore('pref', 'Preferences');
    expect(exact).toBeGreaterThan(partial);
  });

  it('empty query scores 0 (we rely on rankCommands to handle empty query)', () => {
    expect(fuzzyScore('', 'Anything')).toBe(0);
  });
});

// ── rankCommands tests ────────────────────────────────────────────────────────

describe('rankCommands', () => {
  const cmds = [
    { id: 'prefs',   label: 'Open Preferences',  run: vi.fn() },
    { id: 'rebuild', label: 'Rebuild Inventory',  run: vi.fn() },
    { id: 'vendors', label: 'Manage Vendors',     run: vi.fn() },
    { id: 'undo',    label: 'Undo',               run: vi.fn(), keywords: ['history', 'revert'] },
  ];

  it('empty query returns all commands in original order', () => {
    const result = rankCommands('', cmds);
    expect(result.map(c => c.id)).toEqual(['prefs', 'rebuild', 'vendors', 'undo']);
  });

  it('filters out commands with no match', () => {
    const result = rankCommands('xyz', cmds);
    expect(result).toHaveLength(0);
  });

  it('sorts by descending score (better match first)', () => {
    // 'pref' strongly matches 'Open Preferences' and weakly if at all the rest
    const result = rankCommands('pref', cmds);
    expect(result[0].id).toBe('prefs');
  });

  it('matches against keywords when label has no match', () => {
    // 'revert' is a keyword of undo; label 'Undo' won't match
    const result = rankCommands('revert', cmds);
    expect(result.some(c => c.id === 'undo')).toBe(true);
  });

  it('label match beats keyword-only match', () => {
    const commands = [
      { id: 'a', label: 'history',  run: vi.fn() },  // label match
      { id: 'b', label: 'Undo',     run: vi.fn(), keywords: ['history'] }, // keyword match
    ];
    const result = rankCommands('history', commands);
    expect(result[0].id).toBe('a');
  });

  it('returns a new array (does not mutate input)', () => {
    const input = [...cmds];
    const result = rankCommands('undo', cmds);
    expect(cmds).toEqual(input); // original unchanged
    expect(result).not.toBe(cmds);
  });
});

// ── CommandPalette DOM tests ──────────────────────────────────────────────────

describe('CommandPalette', () => {
  let palette;
  let runA, runB, runC;
  let cmds;

  beforeEach(() => {
    document.body.innerHTML = '';
    vi.clearAllMocks();

    runA = vi.fn();
    runB = vi.fn();
    runC = vi.fn();

    cmds = [
      { id: 'a', label: 'Open Preferences', group: 'Global', run: runA },
      { id: 'b', label: 'Rebuild Inventory', group: 'Global', run: runB },
      { id: 'c', label: 'Manage Vendors', group: 'Global', run: runC },
    ];

    palette = CommandPalette({ getCommands: () => cmds });
  });

  afterEach(() => {
    if (palette.isOpen()) palette.close();
  });

  // ── open / close ────────────────────────────────────────────────────────────

  it('isOpen() returns false before open()', () => {
    expect(palette.isOpen()).toBe(false);
  });

  it('open() mounts overlay in the DOM', () => {
    palette.open({});
    const overlay = document.querySelector('.cp-overlay');
    expect(overlay).toBeTruthy();
    expect(palette.isOpen()).toBe(true);
  });

  it('open() populates the list with all commands when no query', () => {
    palette.open({});
    const items = document.querySelectorAll('.cp-item');
    expect(items.length).toBe(3);
  });

  it('open() renders group labels', () => {
    palette.open({});
    const groups = document.querySelectorAll('.cp-group');
    expect(groups.length).toBeGreaterThan(0);
    expect(groups[0].textContent).toBe('Global');
  });

  it('close() removes overlay from DOM', () => {
    palette.open({});
    palette.close();
    expect(document.querySelector('.cp-overlay')).toBeNull();
    expect(palette.isOpen()).toBe(false);
  });

  it('close() releases focus trap', () => {
    palette.open({});
    palette.close();
    expect(releaseMock).toHaveBeenCalled();
  });

  it('open() activates focus trap', () => {
    palette.open({});
    expect(trapMock).toHaveBeenCalled();
  });

  it('Escape closes the palette', () => {
    palette.open({});
    const overlay = document.querySelector('.cp-overlay');
    overlay.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(palette.isOpen()).toBe(false);
  });

  it('click-outside closes the palette', () => {
    palette.open({});
    const overlay = document.querySelector('.cp-overlay');
    // Click on the overlay backdrop (not the inner dialog)
    overlay.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    expect(palette.isOpen()).toBe(false);
  });

  // ── typing / filtering ───────────────────────────────────────────────────────

  it('typing in the search input filters the list', () => {
    palette.open({});
    const input = document.querySelector('.cp-search');
    input.value = 'pref';
    input.dispatchEvent(new Event('input', { bubbles: true }));

    const items = document.querySelectorAll('.cp-item');
    // Only "Open Preferences" should remain
    expect(items.length).toBe(1);
    expect(items[0].textContent).toContain('Preferences');
  });

  it('typing a non-matching query shows empty list', () => {
    palette.open({});
    const input = document.querySelector('.cp-search');
    input.value = 'xyzxyz';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    const items = document.querySelectorAll('.cp-item');
    expect(items.length).toBe(0);
  });

  it('clearing the search restores all commands', () => {
    palette.open({});
    const input = document.querySelector('.cp-search');
    input.value = 'pref';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.value = '';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    const items = document.querySelectorAll('.cp-item');
    expect(items.length).toBe(3);
  });

  // ── keyboard navigation ──────────────────────────────────────────────────────

  it('Down arrow moves active item', () => {
    palette.open({});
    const overlay = document.querySelector('.cp-overlay');
    overlay.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    const items = document.querySelectorAll('.cp-item');
    // After one Down, first item is active
    expect(items[0].classList.contains('cp-active')).toBe(true);
  });

  it('Up arrow wraps from first item to last', () => {
    palette.open({});
    const overlay = document.querySelector('.cp-overlay');
    // Move down once to make first active, then Up to wrap to last
    overlay.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    overlay.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
    const items = document.querySelectorAll('.cp-item');
    expect(items[items.length - 1].classList.contains('cp-active')).toBe(true);
  });

  it('Down arrow wraps from last item to first', () => {
    palette.open({});
    const overlay = document.querySelector('.cp-overlay');
    const totalCmds = cmds.length;
    // Press Down enough times to get past the last item (wraps)
    for (let i = 0; i <= totalCmds; i++) {
      overlay.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    }
    const items = document.querySelectorAll('.cp-item');
    expect(items[0].classList.contains('cp-active')).toBe(true);
  });

  it('Enter runs the active command and closes palette', () => {
    palette.open({});
    const overlay = document.querySelector('.cp-overlay');
    overlay.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    overlay.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(runA).toHaveBeenCalledTimes(1);
    expect(palette.isOpen()).toBe(false);
  });

  it('Enter on second item runs that command', () => {
    palette.open({});
    const overlay = document.querySelector('.cp-overlay');
    overlay.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    overlay.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    overlay.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(runB).toHaveBeenCalledTimes(1);
    expect(runA).not.toHaveBeenCalled();
  });

  it('Enter without active item does nothing harmful', () => {
    palette.open({});
    const overlay = document.querySelector('.cp-overlay');
    // No Down pressed, so no active item
    overlay.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    // Palette may still be open (no active item) — no run should fire
    expect(runA).not.toHaveBeenCalled();
    expect(runB).not.toHaveBeenCalled();
  });

  // ── mouse interaction ────────────────────────────────────────────────────────

  it('hovering an item makes it active', () => {
    palette.open({});
    const items = document.querySelectorAll('.cp-item');
    items[1].dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
    expect(items[1].classList.contains('cp-active')).toBe(true);
    expect(items[0].classList.contains('cp-active')).toBe(false);
  });

  it('clicking an item runs its command and closes palette', () => {
    palette.open({});
    const items = document.querySelectorAll('.cp-item');
    items[2].dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(runC).toHaveBeenCalledTimes(1);
    expect(palette.isOpen()).toBe(false);
  });

  // ── error surfacing ──────────────────────────────────────────────────────────

  it('run() errors are surfaced via AppLog.error and palette still closes', async () => {
    const errorRun = vi.fn(() => { throw new Error('kaboom'); });
    const errCmds = [{ id: 'err', label: 'Bad Command', run: errorRun }];
    const errPalette = CommandPalette({ getCommands: () => errCmds });
    errPalette.open({});
    const overlay = document.querySelector('.cp-overlay');
    overlay.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    overlay.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    // Allow microtask queue to flush for async errors
    await Promise.resolve();
    expect(AppLogMock.error).toHaveBeenCalled();
    expect(errPalette.isOpen()).toBe(false);
  });

  it('async run() rejection is surfaced via AppLog.error', async () => {
    const asyncErrorRun = vi.fn(async () => { throw new Error('async-kaboom'); });
    const errCmds = [{ id: 'err', label: 'Async Bad', run: asyncErrorRun }];
    const errPalette = CommandPalette({ getCommands: () => errCmds });
    errPalette.open({});
    const overlay = document.querySelector('.cp-overlay');
    overlay.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    overlay.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await new Promise(r => setTimeout(r, 10));
    expect(AppLogMock.error).toHaveBeenCalled();
  });

  // ── context-dependent commands ───────────────────────────────────────────────

  it('re-opening rebuilds from fresh getCommands(context)', () => {
    let callCount = 0;
    const dynamic = CommandPalette({
      getCommands: (ctx) => {
        callCount++;
        return ctx.focusedPartKey
          ? [{ id: 'adjust', label: 'Adjust ' + ctx.focusedPartKey, run: vi.fn() }]
          : [{ id: 'prefs', label: 'Open Preferences', run: vi.fn() }];
      },
    });

    dynamic.open({});
    expect(document.querySelector('.cp-item').textContent).toContain('Open Preferences');
    dynamic.close();

    dynamic.open({ focusedPartKey: 'C1' });
    expect(document.querySelector('.cp-item').textContent).toContain('Adjust C1');
    dynamic.close();

    expect(callCount).toBe(2);
  });

  // ── escaping ─────────────────────────────────────────────────────────────────

  it('label text is HTML-escaped to prevent XSS', () => {
    const xssCmds = [{ id: 'x', label: '<script>alert(1)</script>', run: vi.fn() }];
    const xssPalette = CommandPalette({ getCommands: () => xssCmds });
    xssPalette.open({});
    const item = document.querySelector('.cp-item');
    // The literal angle brackets must NOT appear as parsed HTML tags
    expect(item.innerHTML).toContain('&lt;script&gt;');
    xssPalette.close();
  });
});
