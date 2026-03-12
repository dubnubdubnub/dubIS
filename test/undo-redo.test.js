import { describe, it, expect } from 'vitest';
import { createContext, runInContext } from 'node:vm';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..');

function fakeEl() {
  return {
    textContent: '', innerHTML: '', className: '',
    classList: { add() {}, remove() {}, contains() { return false; }, toggle() {} },
    addEventListener() {},
    appendChild() {},
    removeChild() {},
    style: {},
    scrollTop: 0, scrollHeight: 0,
    children: { length: 0 },
    dataset: {},
    querySelectorAll: () => [],
    querySelector: () => null,
  };
}

function loadMain() {
  const sandbox = {
    Map, Set, Array, Object, String, Number, Boolean, Math,
    parseInt, parseFloat, isNaN, NaN, Infinity, undefined,
    JSON, console, RegExp, Date, Error, TypeError, RangeError,
    setTimeout: () => {}, clearTimeout: () => {},
    setInterval: () => {}, clearInterval: () => {},
    navigator: { platform: 'Win32' },
    window: { addEventListener() {} },
    document: {
      readyState: 'loading',
      getElementById: () => fakeEl(),
      createElement: () => fakeEl(),
      addEventListener() {},
      querySelectorAll: () => [],
    },
  };
  const ctx = createContext(sandbox);
  const code = readFileSync(join(ROOT, 'js/main.js'), 'utf-8');
  runInContext(code, ctx, { filename: 'js/main.js' });
  // const/let declarations don't land on the sandbox — expose them explicitly
  runInContext('this.UndoRedo = UndoRedo; this.App = App; this.snapshotLinks = snapshotLinks;', ctx);
  return ctx;
}

describe('UndoRedo.popLast', () => {
  it('returns the most recent entry and removes it', () => {
    const g = loadMain();
    g.UndoRedo.save('test', { v: 1 });
    g.UndoRedo.save('test', { v: 2 });

    const popped = g.UndoRedo.popLast();
    expect(popped.panel).toBe('test');
    expect(popped.data).toEqual({ v: 2 });
    expect(g.UndoRedo._undo.length).toBe(1);
  });

  it('returns undefined on empty stack', () => {
    const g = loadMain();
    expect(g.UndoRedo.popLast()).toBeUndefined();
  });
});

describe('snapshotLinks', () => {
  it('returns a deep clone of manualLinks and confirmedMatches', () => {
    const g = loadMain();
    g.App.links.manualLinks = [{ bomKey: 'a', invPartKey: 'b' }];
    g.App.links.confirmedMatches = [{ bomKey: 'c', invPartKey: 'd' }];

    const snap = g.snapshotLinks();

    expect(snap).toEqual({
      manualLinks: [{ bomKey: 'a', invPartKey: 'b' }],
      confirmedMatches: [{ bomKey: 'c', invPartKey: 'd' }],
    });

    // Mutate originals — snapshot must be unaffected
    g.App.links.manualLinks.push({ bomKey: 'x', invPartKey: 'y' });
    g.App.links.confirmedMatches[0].bomKey = 'CHANGED';

    expect(snap.manualLinks).toEqual([{ bomKey: 'a', invPartKey: 'b' }]);
    expect(snap.confirmedMatches).toEqual([{ bomKey: 'c', invPartKey: 'd' }]);
  });
});
