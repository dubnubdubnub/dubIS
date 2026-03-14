// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../js/ui-helpers.js', () => ({
  showToast: vi.fn(),
  escHtml: vi.fn(s => s || ''),
}));

import { AppLog, api } from '../../js/api.js';
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
