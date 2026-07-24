// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../js/api.js', () => ({
  api: vi.fn(async () => ({})),
  AppLog: { warn: vi.fn(), error: vi.fn() },
}));
vi.mock('../../js/store.js', () => ({
  getBehaviorPrefs: vi.fn(() => ({ autoCopySelection: false })),
}));

let mod;
beforeEach(async () => { vi.resetModules(); mod = await import('../../js/text-popover.js'); document.body.innerHTML = ''; });

describe('isLeafTextElement', () => {
  it('true for a leaf element with text', () => {
    document.body.innerHTML = '<span id="s">hello</span>';
    expect(mod.isLeafTextElement(document.getElementById('s'))).toBe(true);
  });
  it('false for a container with child elements', () => {
    document.body.innerHTML = '<div id="d"><span>a</span></div>';
    expect(mod.isLeafTextElement(document.getElementById('d'))).toBe(false);
  });
  it('false for empty/whitespace text', () => {
    document.body.innerHTML = '<span id="s">   </span>';
    expect(mod.isLeafTextElement(document.getElementById('s'))).toBe(false);
  });
  it('false for input/textarea/select', () => {
    document.body.innerHTML = '<input id="i" value="x">';
    expect(mod.isLeafTextElement(document.getElementById('i'))).toBe(false);
  });
  it('false for null', () => {
    expect(mod.isLeafTextElement(null)).toBe(false);
  });
});

describe('isInteractive', () => {
  it('true for a button', () => {
    document.body.innerHTML = '<button id="b">x</button>';
    expect(mod.isInteractive(document.getElementById('b'))).toBe(true);
  });
  it('true when an ancestor is a link', () => {
    document.body.innerHTML = '<a href="#"><span id="s">x</span></a>';
    expect(mod.isInteractive(document.getElementById('s'))).toBe(true);
  });
  it('false for a plain span', () => {
    document.body.innerHTML = '<span id="s">x</span>';
    expect(mod.isInteractive(document.getElementById('s'))).toBe(false);
  });
});

describe('copyText', () => {
  it('uses navigator.clipboard when available', async () => {
    const writeText = vi.fn(async () => {});
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    const ok = await mod.copyText('abc');
    expect(ok).toBe(true);
    expect(writeText).toHaveBeenCalledWith('abc');
    vi.unstubAllGlobals();
  });
});
