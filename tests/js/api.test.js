// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../js/ui-helpers.js', () => ({
  showToast: vi.fn(),
  escHtml: vi.fn(s => s || ''),
}));

import { AppLog, api, whenPywebviewReady } from '../../js/api.js';
import { showToast } from '../../js/ui-helpers.js';

describe('AppLog', () => {
  beforeEach(() => {
    AppLog.clear();
  });

  it('adds info entries', () => {
    AppLog.info('test message');
    expect(AppLog._entries).toHaveLength(1);
    expect(AppLog._entries[0].level).toBe('info');
    expect(AppLog._entries[0].msg).toBe('test message');
  });

  it('adds warn entries', () => {
    AppLog.warn('warning');
    expect(AppLog._entries[0].level).toBe('warn');
  });

  it('adds error entries', () => {
    AppLog.error('error');
    expect(AppLog._entries[0].level).toBe('error');
  });

  it('timestamps entries', () => {
    AppLog.info('timed');
    expect(AppLog._entries[0].time).toBeInstanceOf(Date);
  });

  it('limits entries to max', () => {
    for (let i = 0; i < 210; i++) {
      AppLog.info('msg ' + i);
    }
    expect(AppLog._entries.length).toBeLessThanOrEqual(200);
  });

  it('clear() empties entries', () => {
    AppLog.info('a');
    AppLog.info('b');
    AppLog.clear();
    expect(AppLog._entries).toHaveLength(0);
  });
});

describe('api()', () => {
  beforeEach(() => {
    AppLog.clear();
    vi.mocked(showToast).mockClear();
  });

  it('calls pywebview bridge method and returns result', async () => {
    window.pywebview = { api: { test_method: vi.fn().mockResolvedValue('result') } };
    const result = await api('test_method', 'arg1', 'arg2');
    expect(window.pywebview.api.test_method).toHaveBeenCalledWith('arg1', 'arg2');
    expect(result).toBe('result');
  });

  it('logs error and shows toast on failure', async () => {
    window.pywebview = { api: { failing: vi.fn().mockRejectedValue(new Error('boom')) } };
    const result = await api('failing');
    expect(result).toBeUndefined();
    expect(AppLog._entries.some(e => e.level === 'error' && e.msg.includes('boom'))).toBe(true);
    expect(showToast).toHaveBeenCalledWith('Error: boom');
  });
});

// Regression tests for the pywebview hydration race.
//
// pywebview injects the bridge in two phases:
//   1. api.js sets `window.pywebview = { api: {} }` — an empty truthy placeholder.
//   2. finish.js calls _createApi(funcList) (attaching method functions) and then
//      dispatches the `pywebviewready` event.
//
// Calling `api('foo')` between phase 1 and phase 2 throws
// "window.pywebview.api[method] is not a function" because the placeholder has no
// methods. Bootstrap code must therefore wait via `whenPywebviewReady()` rather
// than checking `window.pywebview && window.pywebview.api` (which is truthy in
// phase 1 already).
describe('whenPywebviewReady', () => {
  beforeEach(() => {
    delete window.pywebview;
  });

  it('resolves immediately when API methods are populated', async () => {
    window.pywebview = { api: { load_preferences: vi.fn() } };
    let resolved = false;
    whenPywebviewReady().then(() => { resolved = true; });
    // Allow pending microtasks to flush
    await Promise.resolve();
    await Promise.resolve();
    expect(resolved).toBe(true);
  });

  it('does NOT resolve when bridge is in placeholder state (api: {})', async () => {
    // Reproduce the race: placeholder created but methods not attached yet
    window.pywebview = { api: {} };
    let resolved = false;
    whenPywebviewReady().then(() => { resolved = true; });
    await Promise.resolve();
    await Promise.resolve();
    expect(resolved).toBe(false);
  });

  it('does NOT resolve when window.pywebview is undefined', async () => {
    let resolved = false;
    whenPywebviewReady().then(() => { resolved = true; });
    await Promise.resolve();
    await Promise.resolve();
    expect(resolved).toBe(false);
  });

  it('resolves after pywebviewready fires when starting from placeholder', async () => {
    window.pywebview = { api: {} };
    const promise = whenPywebviewReady();
    let resolved = false;
    promise.then(() => { resolved = true; });
    await Promise.resolve();
    expect(resolved).toBe(false);

    // Simulate finish.js: attach methods then dispatch the ready event
    window.pywebview.api.load_preferences = vi.fn();
    window.dispatchEvent(new Event('pywebviewready'));

    await promise;
    expect(resolved).toBe(true);
  });

  it('treats a truthy non-function load_preferences as not ready', async () => {
    // Defensive: only an actual function counts as hydrated. An accidental
    // truthy placeholder value (string, object, etc.) must not pass the check.
    window.pywebview = { api: { load_preferences: 'not-a-function' } };
    let resolved = false;
    whenPywebviewReady().then(() => { resolved = true; });
    await Promise.resolve();
    await Promise.resolve();
    expect(resolved).toBe(false);
  });
});
