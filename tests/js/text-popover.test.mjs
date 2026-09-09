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

describe('isLeafTextElement + isInteractive combined (mouseover-guard rationale)', () => {
  it('isLeafTextElement alone does NOT exclude a button with text — isInteractive is the guard that must', () => {
    document.body.innerHTML = '<button id="b">Adjust</button>';
    var btn = document.getElementById('b');
    expect(mod.isLeafTextElement(btn)).toBe(true);
    expect(mod.isInteractive(btn)).toBe(true);
  });
});

describe('ownText / hasOwnText', () => {
  it('ownText returns only the element\'s own direct text', () => {
    document.body.innerHTML = '<span id="s"><button>⚠</button>30</span>';
    expect(mod.ownText(document.getElementById('s'))).toBe('30');
  });

  it('hasOwnText accepts a value that sits next to a control in the same cell', () => {
    // The real shape: a qty cell whose "no price data" button lives beside the
    // number. isLeafTextElement rejects it (it has an element child), which used
    // to leave that quantity with no copy affordance at all.
    document.body.innerHTML =
      '<span id="q" class="part-qty"><button class="price-warn-btn">⚠</button>30</span>';
    const cell = document.getElementById('q');
    expect(mod.isLeafTextElement(cell)).toBe(false);
    expect(mod.hasOwnText(cell)).toBe(true);
    expect(mod.ownText(cell)).toBe('30');
  });

  it('hasOwnText rejects a pure container whose text all belongs to children', () => {
    document.body.innerHTML = '<div id="d"><span>a</span><span>b</span></div>';
    expect(mod.hasOwnText(document.getElementById('d'))).toBe(false);
  });

  it('hasOwnText rejects whitespace-only direct text between children', () => {
    document.body.innerHTML = '<div id="d"><span>a</span>\n  <span>b</span></div>';
    expect(mod.hasOwnText(document.getElementById('d'))).toBe(false);
  });

  it('hasOwnText rejects form controls and null', () => {
    document.body.innerHTML = '<input id="i" value="x"><textarea id="t">y</textarea>';
    expect(mod.hasOwnText(document.getElementById('i'))).toBe(false);
    expect(mod.hasOwnText(document.getElementById('t'))).toBe(false);
    expect(mod.hasOwnText(null)).toBe(false);
    expect(mod.ownText(null)).toBe('');
  });
});

describe('the hover corridor is re-exported through this module\'s dependency', () => {
  it('copyText here is the shared implementation, not a second clipboard path', async () => {
    const shared = await import('../../js/hover-affordance.js');
    expect(mod.copyText).toBe(shared.copyText);
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
