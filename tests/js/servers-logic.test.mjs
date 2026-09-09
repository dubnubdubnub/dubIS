import { describe, it, expect, vi } from 'vitest';

import {
  LOCAL_ID,
  normalizeServerUrl,
  nameFromUrl,
  normalizeServers,
  addServerEntry,
  updateServerEntry,
  removeServerEntry,
  serverRows,
  probePlan,
  classifyProbe,
  selectionStatus,
} from '../../js/servers-logic.js';

const A = { id: 'a', name: 'Cluster', url: 'https://dubis.example.ts.net' };
const B = { id: 'b', name: 'Bench', url: 'http://192.168.1.9:7890' };

describe('normalizeServerUrl', () => {
  it('keeps an http(s) URL and strips trailing slashes', () => {
    expect(normalizeServerUrl('https://x.example/')).toBe('https://x.example');
    expect(normalizeServerUrl('  http://127.0.0.1:7890//  ')).toBe('http://127.0.0.1:7890');
  });

  it('rejects anything without an http(s) scheme', () => {
    // A scheme-less value would resolve against the app's own origin, which
    // means "remote server" would silently mean "the local one".
    for (const bad of ['x.example', 'ws://x.example', '//x.example', 'ftp://x', '']) {
      expect(normalizeServerUrl(bad)).toBe('');
    }
  });

  it('tolerates non-strings', () => {
    expect(normalizeServerUrl(null)).toBe('');
    expect(normalizeServerUrl(undefined)).toBe('');
    expect(normalizeServerUrl(42)).toBe('');
  });
});

describe('nameFromUrl', () => {
  it('uses the host and port', () => {
    expect(nameFromUrl('https://dubis.example.ts.net')).toBe('dubis.example.ts.net');
    expect(nameFromUrl('http://192.168.1.9:7890/v1')).toBe('192.168.1.9:7890');
  });

  it('is empty for an unusable URL', () => {
    expect(nameFromUrl('nope')).toBe('');
  });
});

describe('normalizeServers', () => {
  it('passes a clean roster through', () => {
    expect(normalizeServers([A, B])).toEqual([A, B]);
  });

  it('returns [] for a missing or non-array value, warning on the latter', () => {
    const warn = vi.fn();
    expect(normalizeServers(undefined, warn)).toEqual([]);
    expect(warn).not.toHaveBeenCalled();
    expect(normalizeServers({ nope: 1 }, warn)).toEqual([]);
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it('drops entries with no id, a bad URL, or a duplicate id/URL', () => {
    const warn = vi.fn();
    const out = normalizeServers([
      A,
      { name: 'no id', url: 'https://y.example' },
      { id: 'c', name: 'bad url', url: 'y.example' },
      { id: 'a', name: 'dup id', url: 'https://z.example' },
      { id: 'd', name: 'dup url', url: 'https://dubis.example.ts.net/' },
      'not an object',
    ], warn);
    expect(out).toEqual([A]);
    expect(warn).toHaveBeenCalledTimes(5);
  });

  it('rejects a persisted entry claiming the reserved local id', () => {
    // The local row is synthesized by serverRows; a persisted twin would render
    // twice and offer two contradictory ways to select the same thing.
    const warn = vi.fn();
    expect(normalizeServers([{ id: LOCAL_ID, name: 'Local', url: 'https://x.example' }], warn))
      .toEqual([]);
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it('derives a missing name from the URL host', () => {
    expect(normalizeServers([{ id: 'a', url: 'https://x.example/' }]))
      .toEqual([{ id: 'a', name: 'x.example', url: 'https://x.example' }]);
    expect(normalizeServers([{ id: 'a', name: '   ', url: 'https://x.example' }])[0].name)
      .toBe('x.example');
  });
});

describe('addServerEntry', () => {
  it('appends without mutating the input', () => {
    const list = [A];
    const r = addServerEntry(list, { id: 'b', name: 'Bench', url: 'http://192.168.1.9:7890/' });
    expect(r.ok).toBe(true);
    expect(r.servers).toEqual([A, B]);
    expect(list).toEqual([A]);
  });

  it('derives the name from the URL when none is given', () => {
    const r = addServerEntry([], { id: 'b', url: 'https://x.example' });
    expect(r.entry).toEqual({ id: 'b', name: 'x.example', url: 'https://x.example' });
  });

  it('rejects a non-http(s) URL with a reason', () => {
    const r = addServerEntry([], { id: 'b', url: 'x.example' });
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/http/);
  });

  it('rejects a URL already in the roster, naming the existing entry', () => {
    // Normalization has to run before the comparison, or a trailing slash
    // makes a second row that selects the same server as the first.
    const r = addServerEntry([A], { id: 'b', url: 'https://dubis.example.ts.net/' });
    expect(r.ok).toBe(false);
    expect(r.reason).toContain('Cluster');
  });

  it('rejects the reserved local id', () => {
    expect(addServerEntry([], { id: LOCAL_ID, url: 'https://x.example' }).ok).toBe(false);
  });
});

describe('updateServerEntry', () => {
  it('renames without touching the URL', () => {
    const r = updateServerEntry([A, B], 'a', { name: 'Home' });
    expect(r.entry).toEqual({ ...A, name: 'Home' });
    expect(r.servers[1]).toEqual(B);
  });

  it('re-points the URL, keeping position', () => {
    const r = updateServerEntry([A, B], 'a', { url: 'https://new.example/' });
    expect(r.servers[0].url).toBe('https://new.example');
    expect(r.servers[0].id).toBe('a');
  });

  it('accepts re-saving an entry with its own URL unchanged', () => {
    // The clash check must exclude the entry being edited, or renaming would
    // fail with "already points at that URL" against itself.
    expect(updateServerEntry([A, B], 'a', { url: A.url, name: 'Home' }).ok).toBe(true);
  });

  it('rejects a URL that collides with a different entry', () => {
    const r = updateServerEntry([A, B], 'b', { url: A.url });
    expect(r.ok).toBe(false);
    expect(r.reason).toContain('Cluster');
  });

  it('rejects a bad URL and an unknown id', () => {
    expect(updateServerEntry([A], 'a', { url: 'nope' }).ok).toBe(false);
    expect(updateServerEntry([A], 'zzz', { name: 'x' }).ok).toBe(false);
  });

  it('re-derives an emptied name from the URL', () => {
    expect(updateServerEntry([A], 'a', { name: '  ' }).entry.name).toBe('dubis.example.ts.net');
  });
});

describe('removeServerEntry', () => {
  it('removes by id and leaves others alone', () => {
    expect(removeServerEntry([A, B], 'a')).toEqual([B]);
    expect(removeServerEntry([A, B], 'zzz')).toEqual([A, B]);
  });
});

describe('serverRows', () => {
  it('always leads with an undeletable Local row', () => {
    const rows = serverRows([], '');
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ id: LOCAL_ID, url: '', selected: true, removable: false });
  });

  it('marks the roster entry whose URL matches the selection', () => {
    const rows = serverRows([A, B], 'https://dubis.example.ts.net');
    expect(rows.map((r) => r.selected)).toEqual([false, true, false]);
    expect(rows[1].removable).toBe(true);
  });

  it('matches the selection through normalization', () => {
    // server_url and the roster entry are normalized by the same function on
    // purpose; a trailing slash on either must not un-select the row.
    expect(serverRows([A], 'https://dubis.example.ts.net/')[1].selected).toBe(true);
  });

  it('synthesizes an unlisted row for a selection not in the roster', () => {
    // DUBIS_URL outranks the preference, and preferences.json can be
    // hand-edited — the server actually in use must never be invisible.
    const rows = serverRows([A], 'https://surprise.example');
    expect(rows).toHaveLength(3);
    expect(rows[2]).toMatchObject({
      url: 'https://surprise.example',
      name: 'surprise.example',
      selected: true,
      unlisted: true,
      removable: false,
    });
    expect(rows[0].selected).toBe(false);
    expect(rows[1].selected).toBe(false);
  });

  it('selects exactly one row in every case', () => {
    for (const sel of ['', A.url, B.url, 'https://unknown.example', 'garbage']) {
      const rows = serverRows([A, B], sel);
      expect(rows.filter((r) => r.selected)).toHaveLength(1);
    }
  });
});

describe('probePlan', () => {
  const LOOPBACK = 'http://127.0.0.1:7890';
  const REMOTE = 'https://dubis.example.ts.net';

  it('calls the Local row active when the page is served from loopback', () => {
    expect(probePlan({ url: '' }, LOOPBACK)).toEqual({ state: 'active', url: LOOPBACK });
    expect(probePlan({ url: '' }, 'http://localhost:5173').state).toBe('active');
  });

  it('calls the Local row dormant — never down — when the page is remote', () => {
    // Nothing is listening locally, but selecting Local *starts* a server. A
    // red dot here would be a lie about a choice that works.
    expect(probePlan({ url: '' }, REMOTE)).toEqual({
      state: 'dormant',
      detail: 'starts with the app',
    });
  });

  it('calls the row we are served from active without probing it', () => {
    expect(probePlan({ url: REMOTE }, REMOTE)).toEqual({ state: 'active', url: REMOTE });
    expect(probePlan({ url: REMOTE + '/' }, REMOTE).state).toBe('active');
  });

  it('refuses to probe an http target from an https page', () => {
    // The browser blocks that as mixed content before it hits the network, so
    // its failure says nothing about the server.
    const plan = probePlan({ url: 'http://192.168.1.9:7890' }, REMOTE);
    expect(plan.state).toBe('blocked');
    expect(plan.detail).toMatch(/https/);
  });

  it('probes an https target from an https page, and anything from http', () => {
    expect(probePlan({ url: 'https://other.example' }, REMOTE))
      .toEqual({ state: 'probe', url: 'https://other.example' });
    expect(probePlan({ url: 'http://192.168.1.9:7890' }, LOOPBACK).state).toBe('probe');
    expect(probePlan({ url: REMOTE }, LOOPBACK).state).toBe('probe');
  });
});

describe('classifyProbe', () => {
  it('calls a dubIS 200 live', () => {
    expect(classifyProbe({ ok: true, status: 200, dubis: true }).state).toBe('live');
  });

  it('calls a 200 without the dubIS payload foreign, not live', () => {
    // Selecting it would restart into a broken app, so "reachable" alone would
    // be the wrong thing to show.
    const c = classifyProbe({ ok: true, status: 200, dubis: false });
    expect(c.state).toBe('foreign');
    expect(c.detail).toBe('not dubIS');
  });

  it('distinguishes an error status, a timeout, and a plain failure', () => {
    expect(classifyProbe({ ok: false, status: 502 }).detail).toBe('HTTP 502');
    // A timeout means something accepted the connection — worth saying.
    expect(classifyProbe({ ok: false, error: 'timed out' }).detail).toBe('timed out');
    expect(classifyProbe({ ok: false }).detail).toBe('unreachable');
    expect(classifyProbe(undefined).state).toBe('down');
  });

  it('does not surface the browser\'s own fetch error text', () => {
    // A failed cross-origin fetch reports "Failed to fetch" whatever went
    // wrong, so passing it through spends the whole (narrow) detail lane
    // saying nothing, and truncates.
    expect(classifyProbe({ ok: false, error: 'Failed to fetch' }).detail).toBe('unreachable');
  });
});

describe('selectionStatus', () => {
  it('is active when the selection is where the page came from', () => {
    expect(selectionStatus('https://x.example', 'https://x.example').pending).toBe(false);
    expect(selectionStatus('', 'http://127.0.0.1:7890').pending).toBe(false);
    expect(selectionStatus('', 'http://localhost:7890').pending).toBe(false);
  });

  it('is pending when a saved change has not been applied yet', () => {
    const s = selectionStatus('https://new.example', 'http://127.0.0.1:7890');
    expect(s.pending).toBe(true);
    expect(s.text).toContain('http://127.0.0.1:7890');
  });

  it('is pending when local is selected but the page is remote', () => {
    expect(selectionStatus('', 'https://x.example').pending).toBe(true);
  });
});
