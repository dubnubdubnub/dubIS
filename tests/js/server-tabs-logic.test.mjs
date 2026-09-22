import { describe, it, expect, vi } from 'vitest';

import {
  LOCAL_ID,
  MERGED_ID,
  LOCAL_LABEL,
  ALL_LABEL,
  normalizeSources,
  sourcesFromRoster,
  allSourceIds,
  tabMembers,
  sourceDot,
  mergedDot,
  tabDot,
  tabKind,
  coversAll,
  tabLabel,
  tabTitle,
  activeSourceValue,
  normalizeTabs,
  seedTabs,
  reconcileTabs,
  resolveActiveTabId,
  tabForSource,
  tabForSourceValue,
  addTab,
  closeTab,
  moveTab,
  groupTabs,
  selectionAfterClick,
  switchIntent,
  shouldShowTabs,
  dropTarget,
  renderTabs,
  switchedMessage,
} from '../../js/server-tabs-logic.js';

/** A source as GET /v1/sources reports one. */
function src(id, over = {}) {
  return {
    id, name: id, url: 'https://' + id + '.example',
    enabled: true, reachable: undefined, detail: '', ...over,
  };
}

/** A tab. */
function tab(id, sources, over = {}) {
  return { id, sources, name: '', view: null, ...over };
}

const BENCH = src('bench');
const SHOP = src('shop');
const TWO = [BENCH, SHOP];
const IDS = [LOCAL_ID, 'bench', 'shop'];

describe('normalizeSources', () => {
  it('reads the enveloped payload, preferring `default` over the `active` alias', () => {
    // `default` is the honest name: it is what a header-less request is served
    // from, not a live "current server" — the hub keeps no such state.
    expect(normalizeSources({ default: 'bench', active: 'shop', sources: [] }).defaultSource)
      .toBe('bench');
    expect(normalizeSources({ active: 'shop', sources: [] }).defaultSource).toBe('shop');
    expect(normalizeSources({ sources: [] }).defaultSource).toBe('');
  });

  it('reads a source entry whole, normalising its URL and name', () => {
    const { sources } = normalizeSources({
      sources: [{ id: 'bench', name: 'Bench', url: 'https://bench.example/', reachable: true, detail: '9 ms' }],
    });
    expect(sources).toEqual([{
      id: 'bench', name: 'Bench', url: 'https://bench.example',
      enabled: true, reachable: true, detail: '9 ms',
      // Carried through from the payload in the hub's own spelling. Absent here,
      // so they take the same "nobody has told us" defaults `reachable` takes.
      has_token: false, auth: 'unknown',
      // Not an ssh:// source, so the hub reports no tunnel behind it.
      tunnel: null,
    }]);
  });

  it('carries an ssh:// source\'s tunnel status through', () => {
    const { sources } = normalizeSources({
      sources: [{
        id: 'box', url: 'ssh://me@box/run/dubis/dubis.sock', reachable: false,
        detail: 'ssh key authentication to box was refused',
        tunnel: { state: 'failed', kind: 'auth', error: 'refused', local_url: '', restarts: 0 },
      }],
    });
    expect(sources[0].url).toBe('ssh://me@box/run/dubis/dubis.sock');
    expect(sources[0].name).toBe('box');
    expect(sources[0].tunnel).toEqual({ state: 'failed', kind: 'auth', error: 'refused' });
  });

  it('accepts a bare array, so a caller can pass a roster it already has', () => {
    const { sources, defaultSource } = normalizeSources([{ id: 'bench', url: 'https://bench.example' }]);
    expect(sources).toHaveLength(1);
    expect(defaultSource).toBe('');
  });

  it('treats a missing reachable as unknown, never as false', () => {
    // The whole point of the tri-state: a red dot is a claim that a server is
    // down, and "the hub has not probed it yet" is not evidence of that.
    const { sources } = normalizeSources([
      { id: 'a', url: 'https://a.example' },
      { id: 'b', url: 'https://b.example', reachable: null },
      { id: 'c', url: 'https://c.example', reachable: 'yes' },
      { id: 'd', url: 'https://d.example', reachable: 0 },
    ]);
    expect(sources.map((s) => s.reachable)).toEqual([undefined, undefined, undefined, undefined]);
  });

  it('keeps a real boolean reachable in both directions', () => {
    const { sources } = normalizeSources([
      { id: 'a', url: 'https://a.example', reachable: true },
      { id: 'b', url: 'https://b.example', reachable: false },
    ]);
    expect(sources.map((s) => s.reachable)).toEqual([true, false]);
  });

  it('defaults enabled to true — the fan-out flag is an opt-out', () => {
    const { sources } = normalizeSources([
      { id: 'a', url: 'https://a.example' },
      { id: 'b', url: 'https://b.example', enabled: false },
    ]);
    expect(sources.map((s) => s.enabled)).toEqual([true, false]);
  });

  it('drops entries that use a reserved id, warning about each', () => {
    const warn = vi.fn();
    const { sources } = normalizeSources([
      { id: LOCAL_ID, url: 'https://a.example' },
      { id: MERGED_ID, url: 'https://b.example' },
      { id: 'keep', url: 'https://c.example' },
    ], warn);
    expect(sources.map((s) => s.id)).toEqual(['keep']);
    expect(warn).toHaveBeenCalledTimes(2);
  });

  it('drops malformed entries rather than failing the whole roster', () => {
    const warn = vi.fn();
    const { sources } = normalizeSources([
      null, 'nope', { url: 'https://noid.example' }, { id: '  ' },
      { id: 'dupe', url: 'https://one.example' },
      { id: 'dupe', url: 'https://two.example' },
      { id: 'good', url: 'https://good.example' },
    ], warn);
    expect(sources.map((s) => s.id)).toEqual(['dupe', 'good']);
    expect(warn.mock.calls.length).toBeGreaterThanOrEqual(5);
  });

  it('names an entry after its host, then its id, when the name is blank', () => {
    const { sources } = normalizeSources([
      { id: 'a', url: 'https://bench.example:7890/' },
      { id: 'b', url: 'not-a-url' },
      { id: 'c', url: 'https://c.example', name: '   ' },
    ]);
    expect(sources.map((s) => s.name)).toEqual(['bench.example:7890', 'b', 'c.example']);
  });

  it('survives junk payloads, including the undefined a failed call returns', () => {
    for (const bad of [undefined, null, 42, 'nope', {}, { sources: 'nope' }]) {
      const { sources, defaultSource } = normalizeSources(bad);
      expect(sources).toEqual([]);
      expect(defaultSource).toBe('');
    }
  });
});

describe('sourcesFromRoster / allSourceIds / tabMembers', () => {
  it('claims nothing about reachability for a roster nobody has contacted', () => {
    expect(sourcesFromRoster([{ id: 'a', name: 'Bench', url: 'https://bench.example/' }])).toEqual([{
      id: 'a', name: 'Bench', url: 'https://bench.example',
      enabled: true, reachable: undefined, detail: '',
      // Same reasoning, one field over: the preferences roster is a list of URLs
      // somebody typed. It records no credential (deliberately — see
      // js/servers-logic.js) and no auth outcome, so both read as "not known".
      has_token: false, auth: 'unknown', tunnel: null,
    }]);
  });

  it("inherits the roster loader's repairs, so the two agree on what is valid", () => {
    expect(sourcesFromRoster([
      { id: 'a', url: 'bench.example' },
      { id: LOCAL_ID, url: 'https://x.example' },
      { id: 'b', url: 'https://b.example' },
    ]).map((s) => s.id)).toEqual(['b']);
    expect(sourcesFromRoster(undefined)).toEqual([]);
  });

  it('puts Local first among the source ids — it is the hub, not a roster entry', () => {
    expect(allSourceIds(TWO)).toEqual([LOCAL_ID, 'bench', 'shop']);
    expect(allSourceIds([])).toEqual([LOCAL_ID]);
  });

  it('resolves a tab to its members in roster order, whatever order it names them', () => {
    // Merge order is the hub's business (roster order), so dragging a tab or
    // grouping right-to-left must not change which server wins a conflict.
    expect(tabMembers(tab('t', ['shop', 'bench']), TWO).map((m) => m.id)).toEqual(['bench', 'shop']);
  });

  it('synthesizes Local, which can never be unreachable', () => {
    const [local] = tabMembers(tab('t', [LOCAL_ID]), TWO);
    expect(local.id).toBe(LOCAL_ID);
    expect(local.reachable).toBe(true);
  });

  it('skips ids that are no longer sources', () => {
    expect(tabMembers(tab('t', ['bench', 'gone']), TWO).map((m) => m.id)).toEqual(['bench']);
  });
});

describe('sourceDot', () => {
  it('lets the active source outrank even a failed probe', () => {
    expect(sourceDot(src('a', { reachable: false }), true).state).toBe('active');
  });

  it('reports what the hub actually found', () => {
    expect(sourceDot(src('a', { reachable: true }), false).state).toBe('live');
    expect(sourceDot(src('a', { reachable: false }), false).state).toBe('down');
  });

  it("passes the hub's own detail through when it has one", () => {
    expect(sourceDot(src('a', { reachable: true, detail: '12 ms' }), false).detail).toBe('12 ms');
  });

  it('is unknown, not down, before anything has been probed', () => {
    expect(sourceDot(src('a'), false).state).toBe('unknown');
    expect(sourceDot(undefined, false).state).toBe('unknown');
  });

  it('is unknown for a source excluded from the fan-out', () => {
    expect(sourceDot(src('a', { enabled: false, reachable: true }), false).state).toBe('unknown');
  });
});

describe('mergedDot', () => {
  it('warns that the view is incomplete when a member is down', () => {
    const dot = mergedDot([src('bench', { reachable: true }), src('shop', { reachable: false })], false);
    expect(dot.state).toBe('partial');
    expect(dot.detail).toContain('shop');
    expect(dot.detail).toContain('incomplete');
  });

  it('still warns while the group is the active tab — that is when it matters most', () => {
    expect(mergedDot([src('shop', { reachable: false })], true).state).toBe('partial');
  });

  it('pluralises when more than one member is down', () => {
    const dot = mergedDot([src('a', { reachable: false }), src('b', { reachable: false })], false);
    expect(dot.detail).toContain('a, b are unreachable');
  });

  it('ignores disabled members — they were never in the merge', () => {
    expect(mergedDot([src('a', { reachable: true }),
      src('b', { enabled: false, reachable: false })], false).state).toBe('live');
  });

  it('is live only when every enabled member answered', () => {
    expect(mergedDot([src('a', { reachable: true }), src('b', { reachable: true })], false).state).toBe('live');
    expect(mergedDot([src('a', { reachable: true }), src('b')], false).state).toBe('unknown');
    expect(mergedDot([], false).state).toBe('unknown');
  });
});

describe('tabDot', () => {
  it('uses the single-source rule for a plain tab and the group rule for a group', () => {
    const sources = [src('bench', { reachable: true }), src('shop', { reachable: false })];
    expect(tabDot(tab('t', ['bench']), sources, false).state).toBe('live');
    expect(tabDot(tab('t', ['shop']), sources, false).state).toBe('down');
    expect(tabDot(tab('t', ['bench', 'shop']), sources, false).state).toBe('partial');
  });

  it('never lets a Local-only tab claim to be unreachable', () => {
    expect(tabDot(tab('t', [LOCAL_ID]), TWO, false).state).toBe('live');
    expect(tabDot(tab('t', [LOCAL_ID]), TWO, true).state).toBe('active');
  });
});

describe('tabKind / coversAll / labels', () => {
  it('is a group only with more than one source', () => {
    expect(tabKind(tab('t', ['bench']))).toBe('single');
    expect(tabKind(tab('t', ['bench', 'shop']))).toBe('group');
  });

  it('answers "covers everything" rather than storing it', () => {
    // Stored, it would go on claiming to be everything the moment a server was
    // added — a lie told by omission.
    expect(coversAll(tab('t', IDS), IDS)).toBe(true);
    expect(coversAll(tab('t', [LOCAL_ID, 'bench']), IDS)).toBe(false);
    expect(coversAll(tab('t', [LOCAL_ID]), [LOCAL_ID])).toBe(false);
  });

  it('labels a plain tab with its server name and a full group "All"', () => {
    expect(tabLabel(tab('t', [LOCAL_ID]), TWO)).toBe(LOCAL_LABEL);
    expect(tabLabel(tab('t', ['bench']), TWO)).toBe('bench');
    expect(tabLabel(tab('t', IDS), TWO)).toBe(ALL_LABEL);
  });

  it('labels a partial group by its members, counting the overflow', () => {
    // A six-server group must not widen the strip by six names — every px of
    // width is a px the user scrolls past to reach the tab after it.
    expect(tabLabel(tab('t', ['bench', 'shop']), TWO)).toBe('bench + shop');
    const many = [src('a'), src('b'), src('c'), src('d')];
    expect(tabLabel(tab('t', ['a', 'b', 'c']), many)).toBe('a + b +1');
  });

  it('prefers an explicit name over anything derived', () => {
    expect(tabLabel(tab('t', ['bench'], { name: '  Prod  ' }), TWO)).toBe('Prod');
  });

  it('says so rather than rendering blank when a tab has lost every source', () => {
    expect(tabLabel(tab('t', ['gone']), TWO)).toBe('Empty');
    expect(tabTitle(tab('t', ['gone']), TWO)).toContain('No sources');
  });

  it('titles a tab with what it actually reads from', () => {
    expect(tabTitle(tab('t', [LOCAL_ID]), TWO)).toContain('own dubIS server');
    expect(tabTitle(tab('t', ['bench']), TWO)).toBe('https://bench.example');
    expect(tabTitle(tab('t', ['bench', 'shop']), TWO)).toBe('Merged across bench, shop');
  });
});

describe('activeSourceValue', () => {
  it('sends a single source by its bare id', () => {
    expect(activeSourceValue(tab('t', ['bench']), IDS)).toBe('bench');
    expect(activeSourceValue(tab('t', [LOCAL_ID]), IDS)).toBe(LOCAL_ID);
  });

  it('sends "merged" for a tab covering everything, not the enumerated set', () => {
    // The legacy token, kept on purpose: it is the hub's own word for "all of
    // them" and stays correct as the roster grows, so retiring the All tab
    // needed no wire change.
    expect(activeSourceValue(tab('t', IDS), IDS)).toBe(MERGED_ID);
  });

  it('sends a comma-joined set for any other subset', () => {
    expect(activeSourceValue(tab('t', ['bench', 'shop']), IDS)).toBe('bench,shop');
    expect(activeSourceValue(tab('t', [LOCAL_ID, 'bench']), IDS)).toBe('local,bench');
  });

  it('never sends nothing', () => {
    expect(activeSourceValue(tab('t', []), IDS)).toBe(LOCAL_ID);
    expect(activeSourceValue(undefined, IDS)).toBe(LOCAL_ID);
  });
});

describe('normalizeTabs', () => {
  it('reads a persisted tab whole', () => {
    const view = { searchTerm: 'cap', distributors: ['lcsc'] };
    expect(normalizeTabs([{ id: 't1', sources: ['bench'], name: 'Prod', view }], IDS))
      .toEqual([{ id: 't1', sources: ['bench'], name: 'Prod', view }]);
  });

  it('drops a source the roster no longer has, keeping the rest of the tab', () => {
    const warn = vi.fn();
    expect(normalizeTabs([{ id: 't1', sources: ['bench', 'gone'] }], IDS, warn))
      .toEqual([{ id: 't1', sources: ['bench'], name: '', view: null }]);
    expect(warn).toHaveBeenCalled();
  });

  it('drops a tab left with no sources at all', () => {
    // Otherwise a tab would switch the hub to a server that does not exist.
    const warn = vi.fn();
    expect(normalizeTabs([{ id: 't1', sources: ['gone'] }], IDS, warn)).toEqual([]);
    expect(warn).toHaveBeenCalled();
  });

  it('drops malformed and duplicate entries rather than crashing the load', () => {
    const warn = vi.fn();
    const out = normalizeTabs([
      null, 'nope', { sources: ['bench'] }, { id: ' ' },
      { id: 't1', sources: ['bench'] },
      { id: 't1', sources: ['shop'] },
      { id: 't2', sources: ['shop', 'shop'] },
    ], IDS, warn);
    expect(out.map((t) => t.id)).toEqual(['t1', 't2']);
    expect(out[1].sources).toEqual(['shop']);
  });

  it('passes the view snapshot through untouched — its shape belongs to saved-views.js', () => {
    const weird = { searchTerm: 'x', somethingNew: 1 };
    expect(normalizeTabs([{ id: 't', sources: ['bench'], view: weird }], IDS)[0].view).toBe(weird);
    expect(normalizeTabs([{ id: 't', sources: ['bench'], view: 'nope' }], IDS)[0].view).toBe(null);
  });

  it('survives junk', () => {
    expect(normalizeTabs(undefined, IDS)).toEqual([]);
    expect(normalizeTabs('nope', IDS, vi.fn())).toEqual([]);
  });
});

describe('seedTabs', () => {
  it('reproduces the old roster-derived strip: Local, each server, then All', () => {
    let n = 0;
    const tabs = seedTabs(IDS, () => 't' + (++n));
    expect(tabs.map((t) => t.sources)).toEqual([[LOCAL_ID], ['bench'], ['shop'], IDS]);
  });

  it('gives a user with no servers exactly one Local tab and no All', () => {
    let n = 0;
    expect(seedTabs([LOCAL_ID], () => 't' + (++n))).toEqual([
      { id: 't1', sources: [LOCAL_ID], name: '', view: null },
    ]);
  });

  it('never seeds nothing', () => {
    expect(seedTabs([], () => 't').length).toBe(1);
  });
});

describe('reconcileTabs', () => {
  it('strips a removed source out of every tab', () => {
    const tabs = [tab('a', [LOCAL_ID, 'shop']), tab('b', ['bench'])];
    expect(reconcileTabs(tabs, IDS, [LOCAL_ID, 'bench']).map((t) => t.sources))
      .toEqual([[LOCAL_ID], ['bench']]);
  });

  it('closes a tab left with nothing', () => {
    const tabs = [tab('a', [LOCAL_ID]), tab('b', ['shop'])];
    expect(reconcileTabs(tabs, IDS, [LOCAL_ID, 'bench']).map((t) => t.id)).toEqual(['a']);
  });

  it('keeps an All tab covering ALL of a grown roster', () => {
    // Otherwise adding a server silently demotes All to "all except the one you
    // just added" — nobody notices until a part goes missing.
    const before = [LOCAL_ID, 'bench'];
    const after = [LOCAL_ID, 'bench', 'shop'];
    const tabs = [tab('all', before), tab('one', ['bench'])];
    const out = reconcileTabs(tabs, before, after);
    expect(out[0].sources).toEqual(after);
    expect(out[1].sources).toEqual(['bench']);
  });

  it('keeps the strip alive rather than returning nothing', () => {
    const out = reconcileTabs([tab('a', ['shop'])], IDS, [LOCAL_ID]);
    expect(out).toHaveLength(1);
    expect(out[0].sources).toEqual([LOCAL_ID]);
  });
});

describe('resolveActiveTabId / tabForSource / tabForSourceValue', () => {
  const tabs = [tab('t1', [LOCAL_ID]), tab('t2', ['bench']), tab('t3', IDS)];

  it('keeps an id that names a real tab, else falls back to the first', () => {
    expect(resolveActiveTabId(tabs, 't2')).toBe('t2');
    expect(resolveActiveTabId(tabs, 'gone')).toBe('t1');
    expect(resolveActiveTabId(tabs, undefined)).toBe('t1');
    expect(resolveActiveTabId([], 't1')).toBe('');
  });

  it('finds the plain tab for a server, and only a plain one', () => {
    expect(tabForSource(tabs, 'bench')).toBe('t2');
    // t3 contains bench but is a group; activating it would show more than the
    // Preferences picker asked for.
    expect(tabForSource(tabs, 'shop')).toBe('');
  });

  it('finds a tab by the source value it would send', () => {
    expect(tabForSourceValue(tabs, IDS, 'bench')).toBe('t2');
    expect(tabForSourceValue(tabs, IDS, MERGED_ID)).toBe('t3');
    expect(tabForSourceValue(tabs, IDS, 'shop')).toBe('');
    expect(tabForSourceValue(tabs, IDS, '')).toBe('');
  });
});

describe('addTab', () => {
  it('opens next to the tab it was opened from, not at the far end', () => {
    const tabs = [tab('a', [LOCAL_ID]), tab('b', ['bench'])];
    expect(addTab(tabs, tab('n', ['shop']), 'a').map((t) => t.id)).toEqual(['a', 'n', 'b']);
  });

  it('appends when the anchor is unknown', () => {
    const tabs = [tab('a', [LOCAL_ID])];
    expect(addTab(tabs, tab('n', ['shop']), 'gone').map((t) => t.id)).toEqual(['a', 'n']);
    expect(addTab(tabs, tab('n', ['shop'])).map((t) => t.id)).toEqual(['a', 'n']);
  });

  it('does not mutate the input', () => {
    const tabs = [tab('a', [LOCAL_ID])];
    addTab(tabs, tab('n', ['shop']), 'a');
    expect(tabs).toHaveLength(1);
  });
});

describe('closeTab', () => {
  const tabs = [tab('a', [LOCAL_ID]), tab('b', ['bench']), tab('c', ['shop'])];

  it('refuses to close the last tab', () => {
    // The grid is always showing something, so there is always a tab saying
    // what that something is.
    const r = closeTab([tab('a', [LOCAL_ID])], 'a', 'a');
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/last tab/i);
  });

  it('activates the right-hand neighbour when closing the active tab', () => {
    const r = closeTab(tabs, 'b', 'b');
    expect(r.tabs.map((t) => t.id)).toEqual(['a', 'c']);
    expect(r.activeTabId).toBe('c');
  });

  it('falls back to the left when the active tab was last', () => {
    expect(closeTab(tabs, 'c', 'c').activeTabId).toBe('b');
  });

  it('leaves the active tab alone when closing another', () => {
    expect(closeTab(tabs, 'a', 'c').activeTabId).toBe('a');
  });

  it('refuses an id that is not open', () => {
    expect(closeTab(tabs, 'a', 'gone').ok).toBe(false);
  });
});

describe('moveTab', () => {
  const tabs = [tab('a', [LOCAL_ID]), tab('b', ['bench']), tab('c', ['shop'])];

  it('moves a tab before another', () => {
    expect(moveTab(tabs, 'c', 'a').map((t) => t.id)).toEqual(['c', 'a', 'b']);
    expect(moveTab(tabs, 'a', 'c').map((t) => t.id)).toEqual(['b', 'a', 'c']);
  });

  it('appends when nothing follows the drop', () => {
    expect(moveTab(tabs, 'a', '').map((t) => t.id)).toEqual(['b', 'c', 'a']);
  });

  it('is a no-op for a tab dropped on itself or an unknown id', () => {
    expect(moveTab(tabs, 'b', 'b').map((t) => t.id)).toEqual(['a', 'b', 'c']);
    expect(moveTab(tabs, 'gone', 'a').map((t) => t.id)).toEqual(['a', 'b', 'c']);
  });

  it('does not mutate the input', () => {
    moveTab(tabs, 'c', 'a');
    expect(tabs.map((t) => t.id)).toEqual(['a', 'b', 'c']);
  });
});

describe('groupTabs', () => {
  const tabs = [
    tab('a', [LOCAL_ID], { view: { searchTerm: 'left' } }),
    tab('b', ['bench']),
    tab('c', ['shop']),
  ];

  it('replaces the grouped tabs with one, at the leftmost position', () => {
    // A rearrangement, not a duplication: leaving the originals would double
    // the strip on every group.
    const r = groupTabs(tabs, ['b', 'c'], 'g');
    expect(r.ok).toBe(true);
    expect(r.tabs.map((t) => t.id)).toEqual(['a', 'g']);
    expect(r.tabs[1].sources).toEqual(['bench', 'shop']);
    expect(r.activeTabId).toBe('g');
  });

  it('unions the sources, de-duplicated, and can group a group', () => {
    const r = groupTabs([tab('a', ['bench', 'shop']), tab('b', ['shop', LOCAL_ID])], ['a', 'b'], 'g');
    expect(r.tabs[0].sources).toEqual(['bench', 'shop', LOCAL_ID]);
  });

  it('inherits the leftmost tab’s view — the filters the user was looking at', () => {
    expect(groupTabs(tabs, ['a', 'c'], 'g').tabs[0].view).toEqual({ searchTerm: 'left' });
  });

  it('refuses a group of fewer than two', () => {
    expect(groupTabs(tabs, ['a'], 'g').ok).toBe(false);
    expect(groupTabs(tabs, [], 'g').reason).toMatch(/two or more/i);
    expect(groupTabs(tabs, ['gone', 'nope'], 'g').ok).toBe(false);
  });
});

describe('selectionAfterClick', () => {
  const order = ['a', 'b', 'c', 'd'];

  it('replaces the selection on a plain click and moves the anchor', () => {
    expect(selectionAfterClick(order, ['a', 'b'], 'a', 'c', {}))
      .toEqual({ selected: ['c'], anchor: 'c' });
  });

  it('toggles one on ctrl/cmd, keeping strip order', () => {
    expect(selectionAfterClick(order, ['c'], 'c', 'a', { ctrl: true }))
      .toEqual({ selected: ['a', 'c'], anchor: 'a' });
    expect(selectionAfterClick(order, ['a', 'c'], 'a', 'c', { ctrl: true }))
      .toEqual({ selected: ['a'], anchor: 'c' });
  });

  it('extends a range on shift, in either direction', () => {
    expect(selectionAfterClick(order, ['b'], 'b', 'd', { shift: true }).selected)
      .toEqual(['b', 'c', 'd']);
    expect(selectionAfterClick(order, ['c'], 'c', 'a', { shift: true }).selected)
      .toEqual(['a', 'b', 'c']);
  });

  it('keeps the anchor put across repeated shift-clicks', () => {
    // Otherwise the second shift-click walks the selection across the strip
    // instead of re-extending from where the user started.
    const first = selectionAfterClick(order, ['a'], 'a', 'd', { shift: true });
    expect(first.anchor).toBe('a');
    expect(selectionAfterClick(order, first.selected, first.anchor, 'b', { shift: true }).selected)
      .toEqual(['a', 'b']);
  });

  it('degrades to a plain click when there is no usable anchor', () => {
    expect(selectionAfterClick(order, [], '', 'c', { shift: true }))
      .toEqual({ selected: ['c'], anchor: 'c' });
    expect(selectionAfterClick(order, [], 'gone', 'c', { shift: true }).selected).toEqual(['c']);
  });
});

describe('switchIntent', () => {
  const tabs = [tab('a', [LOCAL_ID]), tab('b', ['bench'])];

  it('is a no-op on the tab you are already on', () => {
    expect(switchIntent(tabs, 'a', 'a')).toEqual({ ok: true, changed: false, tabId: 'a' });
  });

  it('switches to another tab', () => {
    expect(switchIntent(tabs, 'a', 'b')).toEqual({ ok: true, changed: true, tabId: 'b' });
  });

  it('refuses a tab that is not open, with something to say', () => {
    const r = switchIntent(tabs, 'a', 'gone');
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/no longer open/);
  });
});

describe('shouldShowTabs', () => {
  it('hides a strip with one tab and nowhere else to go', () => {
    expect(shouldShowTabs([tab('a', [LOCAL_ID])], [LOCAL_ID])).toBe(false);
    expect(shouldShowTabs([], [LOCAL_ID])).toBe(false);
    expect(shouldShowTabs(undefined, undefined)).toBe(false);
  });

  it('shows as soon as a second tab exists, or a second server does', () => {
    expect(shouldShowTabs([tab('a', [LOCAL_ID]), tab('b', [LOCAL_ID])], [LOCAL_ID])).toBe(true);
    expect(shouldShowTabs([tab('a', [LOCAL_ID])], [LOCAL_ID, 'bench'])).toBe(true);
  });
});

describe('dropTarget', () => {
  const rect = { left: 100, width: 60 }; // midpoint 130

  it('drops before the hovered tab on its left half', () => {
    expect(dropTarget(101, rect, 'b', 'c')).toBe('b');
    expect(dropTarget(129, rect, 'b', 'c')).toBe('b');
  });

  it('drops after it on its right half', () => {
    expect(dropTarget(131, rect, 'b', 'c')).toBe('c');
    expect(dropTarget(159, rect, 'b', 'c')).toBe('c');
  });

  it('drops last past the final tab', () => {
    expect(dropTarget(159, rect, 'b', '')).toBe('');
  });

  it('is scale-invariant, which is what keeps the zoom seam out of the drag', () => {
    // Both inputs come from ONE coordinate space (js/server-tabs.js converts
    // each to authored px first). Scaling them together must not change the
    // answer — that is the property a mixed-space bug would break, and it would
    // look perfect at zoom 1 where the two spaces coincide.
    for (const z of [0.5, 1, 1.25, 2]) {
      const scaled = { left: rect.left * z, width: rect.width * z };
      expect(dropTarget(101 * z, scaled, 'b', 'c')).toBe('b');
      expect(dropTarget(159 * z, scaled, 'b', 'c')).toBe('c');
    }
  });
});

describe('renderTabs', () => {
  const tabs = [tab('t1', [LOCAL_ID]), tab('t2', ['bench']), tab('t3', IDS)];

  it('renders every tab in order, marking exactly one active', () => {
    const models = renderTabs(tabs, TWO, 't2');
    expect(models.map((m) => m.id)).toEqual(['t1', 't2', 't3']);
    expect(models.filter((m) => m.active).map((m) => m.id)).toEqual(['t2']);
    expect(models.map((m) => m.label)).toEqual([LOCAL_LABEL, 'bench', ALL_LABEL]);
    expect(models.map((m) => m.kind)).toEqual(['single', 'single', 'group']);
  });

  it('carries the multi-selection separately from the active tab', () => {
    // Two different ideas: "the grid is showing this" vs "this is picked for
    // grouping". They must not read the same.
    const models = renderTabs(tabs, TWO, 't1', ['t2', 't3']);
    expect(models.map((m) => m.selected)).toEqual([false, true, true]);
    expect(models.map((m) => m.active)).toEqual([true, false, false]);
  });

  it('marks tabs closable only while more than one is open', () => {
    expect(renderTabs(tabs, TWO, 't1').every((m) => m.closable)).toBe(true);
    expect(renderTabs([tabs[0]], TWO, 't1')[0].closable).toBe(false);
  });

  it('falls back to the first tab for an active id nothing matches', () => {
    expect(renderTabs(tabs, TWO, 'gone').find((m) => m.active).id).toBe('t1');
  });

  it('tolerates a missing tab set', () => {
    expect(renderTabs(undefined, TWO, '')).toEqual([]);
  });
});

describe('switchedMessage', () => {
  const tabs = [tab('t1', [LOCAL_ID]), tab('t2', IDS)];

  it('names what the tab shows', () => {
    expect(switchedMessage(tabs, TWO, 't1')).toBe('Now showing ' + LOCAL_LABEL);
    expect(switchedMessage(tabs, TWO, 't2')).toBe('Now showing ' + ALL_LABEL);
  });

  it('degrades to something sayable for an unknown id', () => {
    expect(switchedMessage(tabs, TWO, 'gone')).toBe('Switched tab');
  });
});
