// @ts-check
/* server-tabs-sequence.test.mjs — the tab strip as a state machine.
 *
 * `tests/js/server-tabs-logic.test.mjs` checks each operation once, in
 * isolation, from a hand-written starting state. That is the right shape for
 * "does closeTab activate the right neighbour", and it is the wrong shape for
 * the bugs tabs actually have, which live in the *sequences*: group two, close
 * one of the survivors, drag the group left, remove the server underneath it,
 * open a duplicate, switch back. Each step is fine; the fifth one is where the
 * strip starts describing something that is not there.
 *
 * So this file does not test operations. It runs randomized sequences of them
 * from a seeded PRNG and asserts a fixed set of invariants after EVERY step —
 * the ones that have to hold no matter what the user just did:
 *
 *   I1  the strip is never empty (the grid is always showing something, and
 *       something always has to name it)
 *   I2  the active tab id always names an open tab
 *   I3  tab ids are unique
 *   I4  every tab names at least one source, with no duplicates, all of them
 *       currently configured — a tab that names a server that is gone would
 *       switch the hub to a source that does not exist
 *   I5  `activeSourceValue` for every tab is a selector the hub's grammar
 *       accepts: one id, `merged`, or a comma-joined set of real ids
 *   I6  a tab covering every source serialises to `merged`, and only then —
 *       otherwise "All" quietly becomes "all except the one you just added"
 *
 * and, per operation, the *locality* claims that make the strip predictable:
 * closing a tab disturbs no other tab's sources or view, reordering changes
 * only order, and grouping replaces exactly its members.
 *
 * Deterministic by construction: a seeded xorshift PRNG, no clock, no DOM, no
 * network. A failure prints the seed and the operation log, which is enough to
 * replay it exactly.
 */

import { describe, it, expect } from 'vitest';
import {
  LOCAL_ID,
  MERGED_ID,
  addTab,
  closeTab,
  moveTab,
  groupTabs,
  reconcileTabs,
  resolveActiveTabId,
  seedTabs,
  coversAll,
  allSourceIds,
  activeSourceValue,
  normalizeTabs,
  selectionAfterClick,
  switchIntent,
} from '../../js/server-tabs-logic.js';

// ── A tiny deterministic PRNG ───────────────────────────────
// Not Math.random: a property test that cannot be replayed is a flake report,
// not a bug report.

/** @param {number} seed */
function rng(seed) {
  let state = (seed >>> 0) || 0x9e3779b9;
  return function next() {
    state ^= state << 13; state >>>= 0;
    state ^= state >>> 17;
    state ^= state << 5; state >>>= 0;
    return state / 0x100000000;
  };
}

/** @param {() => number} rand @param {Array<any>} list */
function pick(rand, list) {
  return list[Math.floor(rand() * list.length) % list.length];
}

// ── The model under test ────────────────────────────────────

const SOURCES = [
  { id: 'bench', name: 'bench', url: 'http://bench.local:7891', enabled: true, reachable: true, detail: '' },
  { id: 'shop', name: 'shop', url: 'https://shop.example', enabled: true, reachable: undefined, detail: '' },
  { id: 'attic', name: 'attic', url: 'http://attic.local:9000', enabled: true, reachable: false, detail: 'asleep' },
];

/**
 * The invariants that hold after every single operation.
 * @param {{tabs: Array<any>, activeTabId: string, sources: Array<any>}} state
 * @param {string} where
 */
function checkInvariants(state, where) {
  const { tabs, activeTabId, sources } = state;
  const ids = allSourceIds(sources);
  const known = new Set(ids);

  expect(tabs.length, `I1 the strip went empty after ${where}`).toBeGreaterThan(0);
  expect(tabs.some((t) => t.id === activeTabId),
    `I2 the active tab ${activeTabId} is not open after ${where}`).toBe(true);
  expect(new Set(tabs.map((t) => t.id)).size,
    `I3 duplicate tab ids after ${where}`).toBe(tabs.length);

  for (const tab of tabs) {
    expect(tab.sources.length, `I4 tab ${tab.id} names no source after ${where}`)
      .toBeGreaterThan(0);
    expect(new Set(tab.sources).size,
      `I4 tab ${tab.id} names a source twice after ${where} — the per-source `
      + 'breakdown would count it twice').toBe(tab.sources.length);
    for (const sid of tab.sources) {
      expect(known.has(sid),
        `I4 tab ${tab.id} still names "${sid}", which is no longer configured, after ${where}`)
        .toBe(true);
    }

    // I5: the selector grammar. `server/proxy.py:parse_source_header` splits on
    // commas and `server/sources.py:Registry.resolve` requires each token to be
    // a real source or the bare keyword — so a value outside this grammar is a
    // 404 the moment the tab is clicked.
    const value = activeSourceValue(tab, ids);
    expect(value, `I5 tab ${tab.id} serialises to nothing after ${where}`).toBeTruthy();
    if (value !== MERGED_ID) {
      for (const token of value.split(',')) {
        expect(known.has(token),
          `I5 tab ${tab.id} sends the unknown selector "${token}" after ${where}`).toBe(true);
      }
    }

    // I6: "All" is asked, never stored.
    expect(value === MERGED_ID,
      `I6 tab ${tab.id} disagrees with coversAll after ${where}`).toBe(coversAll(tab, ids));
  }
}

let counter = 0;
const newId = () => 'tab' + (++counter);

/**
 * Apply one randomly chosen operation. Returns the new state plus a label.
 * @param {() => number} rand
 * @param {{tabs: Array<any>, activeTabId: string, sources: Array<any>}} state
 */
function step(rand, state) {
  const { tabs, activeTabId, sources } = state;
  const ids = allSourceIds(sources);
  const ops = ['open', 'duplicate', 'close', 'move', 'group', 'switch', 'roster-remove', 'roster-add'];
  const op = pick(rand, ops);

  switch (op) {
    case 'open': {
      const want = [pick(rand, ids)];
      const tab = { id: newId(), sources: want, name: '', view: null };
      return { label: `open(${want})`, next: { ...state, tabs: addTab(tabs, tab, activeTabId), activeTabId: tab.id } };
    }
    case 'duplicate': {
      // `+` with no argument: another view of what the active tab shows. The
      // whole reason a tab id is not a source id.
      const active = tabs.find((t) => t.id === activeTabId) || tabs[0];
      const tab = { id: newId(), sources: active.sources.slice(), name: '', view: null };
      return { label: `duplicate(${active.id})`, next: { ...state, tabs: addTab(tabs, tab, activeTabId), activeTabId: tab.id } };
    }
    case 'close': {
      const victim = pick(rand, tabs).id;
      const before = tabs.filter((t) => t.id !== victim).map((t) => JSON.stringify(t));
      const result = closeTab(tabs, activeTabId, victim);
      if (!result.ok) return { label: `close(${victim}) refused`, next: state };
      // Locality: closing one tab must not touch any other.
      expect(result.tabs.map((t) => JSON.stringify(t)),
        `close(${victim}) disturbed a surviving tab`).toEqual(before);
      return { label: `close(${victim})`, next: { ...state, tabs: result.tabs, activeTabId: result.activeTabId } };
    }
    case 'move': {
      const moved = pick(rand, tabs).id;
      const beforeId = rand() < 0.3 ? '' : pick(rand, tabs).id;
      const out = moveTab(tabs, moved, beforeId);
      // Locality: reordering changes order and nothing else.
      const sortById = (list) => list.map((t) => JSON.stringify(t)).sort();
      expect(sortById(out), `move(${moved}) changed more than the order`).toEqual(sortById(tabs));
      return { label: `move(${moved} before ${beforeId || 'end'})`, next: { ...state, tabs: out } };
    }
    case 'group': {
      const howMany = 1 + Math.floor(rand() * Math.min(3, tabs.length));
      const chosen = [];
      for (const tab of tabs) {
        if (chosen.length < howMany && rand() < 0.6) chosen.push(tab.id);
      }
      const result = groupTabs(tabs, chosen, newId());
      if (!result.ok) return { label: `group(${chosen}) refused`, next: state };
      // Locality: grouping replaces exactly its members.
      const untouched = tabs.filter((t) => !chosen.includes(t.id)).map((t) => JSON.stringify(t));
      const survivors = result.tabs.filter((t) => !chosen.includes(t.id) && t.id !== result.activeTabId)
        .map((t) => JSON.stringify(t));
      expect(survivors, `group(${chosen}) disturbed a tab it did not name`).toEqual(untouched);
      const group = result.tabs.find((t) => t.id === result.activeTabId);
      const union = new Set(tabs.filter((t) => chosen.includes(t.id)).flatMap((t) => t.sources));
      expect(new Set(group.sources), `group(${chosen}) is not the union of its members`).toEqual(union);
      return { label: `group(${chosen})`, next: { ...state, tabs: result.tabs, activeTabId: result.activeTabId } };
    }
    case 'switch': {
      const target = pick(rand, tabs).id;
      const intent = switchIntent(tabs, activeTabId, target);
      expect(intent.ok, `switchIntent refused an open tab ${target}`).toBe(true);
      return { label: `switch(${target})`, next: { ...state, activeTabId: target } };
    }
    case 'roster-remove': {
      if (sources.length <= 1) return { label: 'roster-remove skipped', next: state };
      const gone = pick(rand, sources).id;
      const after = sources.filter((s) => s.id !== gone);
      const tabsAfter = reconcileTabs(tabs, allSourceIds(sources), allSourceIds(after));
      const kept = tabsAfter.length ? tabsAfter : seedTabs(allSourceIds(after), newId);
      return {
        label: `roster-remove(${gone})`,
        next: { tabs: kept, activeTabId: resolveActiveTabId(kept, activeTabId), sources: after },
      };
    }
    case 'roster-add':
    default: {
      const missing = SOURCES.filter((s) => !sources.some((x) => x.id === s.id));
      if (!missing.length) return { label: 'roster-add skipped', next: state };
      const after = [...sources, missing[0]];
      const tabsAfter = reconcileTabs(tabs, allSourceIds(sources), allSourceIds(after));
      const kept = tabsAfter.length ? tabsAfter : seedTabs(allSourceIds(after), newId);
      return {
        label: `roster-add(${missing[0].id})`,
        next: { tabs: kept, activeTabId: resolveActiveTabId(kept, activeTabId), sources: after },
      };
    }
  }
}

describe('server tabs: randomized operation sequences', () => {
  for (const seed of [1, 7, 42, 1337, 90210, 0xbeef, 20260919]) {
    it(`holds every invariant across 120 operations (seed ${seed})`, () => {
      counter = 0;
      const rand = rng(seed);
      const sources = SOURCES.slice(0, 1 + Math.floor(rand() * SOURCES.length));
      let state = {
        tabs: seedTabs(allSourceIds(sources), newId),
        activeTabId: '',
        sources,
      };
      state.activeTabId = resolveActiveTabId(state.tabs, '');
      checkInvariants(state, 'the seeded strip');

      const log = [];
      for (let i = 0; i < 120; i += 1) {
        const { label, next } = step(rand, state);
        log.push(label);
        state = next;
        try {
          checkInvariants(state, label);
        } catch (e) {
          throw new Error(
            `seed ${seed}, step ${i}: ${e.message}\n\nlog:\n  ` + log.join('\n  '),
          );
        }
      }
    });
  }
});

describe('server tabs: what survives a round trip through preferences', () => {
  /* The strip is persisted into `preferences.server_tabs` and read back by
     `normalizeTabs` against whatever roster exists at that moment. Everything
     the user built has to come back, and nothing that has since become
     meaningless may. */

  it('round-trips an arbitrary strip unchanged while the roster is unchanged', () => {
    counter = 0;
    const rand = rng(31337);
    const sources = SOURCES;
    const ids = allSourceIds(sources);
    let state = { tabs: seedTabs(ids, newId), activeTabId: '', sources };
    state.activeTabId = resolveActiveTabId(state.tabs, '');
    for (let i = 0; i < 60; i += 1) state = step(rand, state).next;

    const persisted = JSON.parse(JSON.stringify(state.tabs));
    const restored = normalizeTabs(persisted, allSourceIds(state.sources));
    expect(restored).toEqual(state.tabs);
    expect(resolveActiveTabId(restored, state.activeTabId)).toBe(state.activeTabId);
  });

  it('never restores a tab pointing at a server that is gone', () => {
    const persisted = [
      { id: 'a', sources: ['local', 'ghost'], name: '', view: null },
      { id: 'b', sources: ['ghost'], name: '', view: null },
    ];
    const restored = normalizeTabs(persisted, [LOCAL_ID]);
    expect(restored.map((t) => t.id)).toEqual(['a']);
    expect(restored[0].sources).toEqual([LOCAL_ID]);
  });

  it('keeps two tabs on the SAME server as two independent tabs', () => {
    /* The feature's stated reason for tab identity: bench filtered to
       capacitors in one tab, bench sorted by value in another. A round trip
       that collapsed them — or that leaked one's view into the other — would
       make `+` a no-op with a new id. */
    const persisted = [
      { id: 'a', sources: ['bench'], name: '', view: { searchTerm: 'cap' } },
      { id: 'b', sources: ['bench'], name: '', view: { searchTerm: '0402' } },
    ];
    const restored = normalizeTabs(persisted, [LOCAL_ID, 'bench']);
    expect(restored).toHaveLength(2);
    expect(restored[0].view).toEqual({ searchTerm: 'cap' });
    expect(restored[1].view).toEqual({ searchTerm: '0402' });

    const closed = closeTab(restored, 'a', 'a');
    expect(closed.ok).toBe(true);
    expect(closed.tabs).toHaveLength(1);
    expect(closed.tabs[0].view).toEqual({ searchTerm: '0402' },
      'closing one tab on a server must not disturb the other one');
  });
});

describe('server tabs: selection is a selection, not a navigation', () => {
  /* ctrl/shift-click picks tabs to group. If it also switched the grid, every
     click in a multi-select would cost an inventory refetch — and, with a
     merged view, a fan-out across every server. */

  it('extends from a fixed anchor however many times you shift-click', () => {
    const order = ['a', 'b', 'c', 'd'];
    let s = selectionAfterClick(order, [], '', 'b', {});
    expect(s).toEqual({ selected: ['b'], anchor: 'b' });
    s = selectionAfterClick(order, s.selected, s.anchor, 'd', { shift: true });
    expect(s).toEqual({ selected: ['b', 'c', 'd'], anchor: 'b' });
    s = selectionAfterClick(order, s.selected, s.anchor, 'c', { shift: true });
    expect(s, 'a second shift-click re-extends from b, it does not walk the range')
      .toEqual({ selected: ['b', 'c'], anchor: 'b' });
  });

  it('keeps the selection in strip order however it was built', () => {
    const order = ['a', 'b', 'c', 'd'];
    let s = { selected: [], anchor: '' };
    for (const id of ['d', 'a', 'c']) {
      s = selectionAfterClick(order, s.selected, s.anchor, id, { ctrl: true });
    }
    expect(s.selected, 'groupTabs takes the leftmost member\'s position and view, '
      + 'so the selection has to be ordered or the group jumps').toEqual(['a', 'c', 'd']);
  });

  it('refuses a group of one rather than renaming a tab', () => {
    const tabs = [{ id: 'a', sources: ['bench'], name: '', view: null }];
    expect(groupTabs(tabs, ['a'], 'new').ok).toBe(false);
  });
});
