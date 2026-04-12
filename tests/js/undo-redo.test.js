import { describe, it, expect, vi } from 'vitest';

// Mock DOM-dependent modules before importing the modules under test
vi.mock('../../js/ui-helpers.js', () => ({
  showToast: vi.fn(),
  escHtml: vi.fn(s => s || ''),
  Modal: vi.fn(),
}));

vi.mock('../../js/constants.js', () => ({
  SECTION_ORDER: [],
  FIELDNAMES: [],
}));

import { UndoRedo } from '../../js/undo-redo.js';
import { store, snapshotLinks } from '../../js/store.js';

describe('UndoRedo.popLast', () => {
  it('returns the most recent entry and removes it', () => {
    UndoRedo._undo = [];
    UndoRedo._redo = [];
    UndoRedo.save('test', { v: 1 });
    UndoRedo.save('test', { v: 2 });

    const popped = UndoRedo.popLast();
    expect(popped.panel).toBe('test');
    expect(popped.data).toEqual({ v: 2 });
    expect(UndoRedo._undo.length).toBe(1);
  });

  it('returns undefined on empty stack', () => {
    UndoRedo._undo = [];
    UndoRedo._redo = [];
    expect(UndoRedo.popLast()).toBeUndefined();
  });
});

describe('snapshotLinks', () => {
  it('returns a deep clone of manualLinks and confirmedMatches', () => {
    store.links.manualLinks = [{ bomKey: 'a', invPartKey: 'b' }];
    store.links.confirmedMatches = [{ bomKey: 'c', invPartKey: 'd' }];

    const snap = snapshotLinks();

    expect(snap).toEqual({
      manualLinks: [{ bomKey: 'a', invPartKey: 'b' }],
      confirmedMatches: [{ bomKey: 'c', invPartKey: 'd' }],
    });

    // Mutate originals — snapshot must be unaffected
    store.links.manualLinks.push({ bomKey: 'x', invPartKey: 'y' });
    store.links.confirmedMatches[0].bomKey = 'CHANGED';

    expect(snap.manualLinks).toEqual([{ bomKey: 'a', invPartKey: 'b' }]);
    expect(snap.confirmedMatches).toEqual([{ bomKey: 'c', invPartKey: 'd' }]);
  });
});
