// ssh:// servers in the roster (js/servers-logic.js), pinned against the SAME
// case table server/ssh_tunnel.py is tested with — so the browser-side and
// hub-side validators can never disagree about which roster entries exist.
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import {
  parseSshUrl,
  isSshUrl,
  urlRejection,
  normalizeServerUrl,
  nameFromUrl,
  normalizeServers,
  addServerEntry,
  updateServerEntry,
  probePlan,
  hubDot,
} from '../../js/servers-logic.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const CASES = JSON.parse(readFileSync(path.join(here, '..', 'fixtures', 'ssh', 'url-cases.json'), 'utf8'));

describe('parseSshUrl — the shared grammar table', () => {
  for (const c of CASES.valid) {
    it('accepts ' + c.url.trim(), () => {
      const r = parseSshUrl(c.url);
      expect(r.ok).toBe(true);
      expect(r.canonical).toBe(c.canonical);
      expect(r.host).toBe(c.host);
      expect(r.user).toBe(c.user);
      expect(r.port).toBe(c.port);
      expect(r.remote).toBe(c.remote);
      expect(r.kind).toBe(c.kind);
      // Canonical is a fixed point, exactly as on the Python side.
      expect(parseSshUrl(r.canonical).canonical).toBe(r.canonical);
      expect(normalizeServerUrl(c.url)).toBe(c.canonical);
    });
  }
  for (const c of CASES.invalid) {
    it('refuses ' + c.url + ' (' + c.reason + ')', () => {
      const r = parseSshUrl(c.url);
      expect(r.ok).toBe(false);
      expect(r.reason.toLowerCase()).toContain(c.reason.toLowerCase());
      expect(normalizeServerUrl(c.url)).toBe('');
      expect(urlRejection(c.url)).toMatch(/^Invalid ssh:\/\/ URL: /);
    });
  }
});

describe('ssh:// in the roster', () => {
  const SSH = 'ssh://isaac@infra.example/run/dubis/dubis.sock';

  it('recognises the scheme case-insensitively', () => {
    expect(isSshUrl('SSH://x/y')).toBe(true);
    expect(isSshUrl('https://x')).toBe(false);
  });

  it('names an ssh server for the box, not the tunnel', () => {
    expect(nameFromUrl(SSH)).toBe('infra.example');
  });

  it('keeps a valid ssh entry and drops an invalid one when loading', () => {
    const warnings = [];
    const out = normalizeServers([
      { id: 'box', name: 'Box', url: SSH + '/' },
      { id: 'bad', name: 'Bad', url: 'ssh://box' },
    ], (m) => warnings.push(m));
    expect(out).toEqual([{ id: 'box', name: 'Box', url: SSH }]);
    expect(warnings.join('\n')).toContain('"bad"');
  });

  it('add/update refuse an invalid ssh URL with the grammar reason', () => {
    const add = addServerEntry([], { id: 's1', url: 'ssh://box' });
    expect(add.ok).toBe(false);
    expect(add.reason).toContain('remote target');
    const ok = addServerEntry([], { id: 's1', url: SSH });
    expect(ok.ok).toBe(true);
    expect(ok.entry.name).toBe('infra.example');
    const upd = updateServerEntry(ok.servers, 's1', { url: 'ssh://me:pw@box/127.0.0.1:1' });
    expect(upd.ok).toBe(false);
    expect(upd.reason).toContain('password');
  });

  it('a plain bad URL still gets the scheme rule, now naming ssh://', () => {
    expect(addServerEntry([], { id: 's1', url: 'ftp://x' }).reason).toContain('ssh://');
  });
});

describe('the picker dot for an ssh server', () => {
  it('is never probed from the page — it defers to the hub', () => {
    expect(probePlan({ url: 'ssh://me@box/127.0.0.1:7891' }, 'http://127.0.0.1:5000'))
      .toEqual({ state: 'hub', detail: 'checked by the hub' });
    // Not "blocked" either, even from an https page: there is nothing to block.
    expect(probePlan({ url: 'ssh://me@box/127.0.0.1:7891' }, 'https://dubis.example').state).toBe('hub');
  });

  it('reads unknown, never red, until the hub has answered', () => {
    expect(hubDot(undefined).state).toBe('unknown');
    expect(hubDot({ reachable: undefined, detail: '' }).state).toBe('unknown');
  });

  it('is live when the hub reached it through the tunnel', () => {
    expect(hubDot({ reachable: true, detail: '', tunnel: { state: 'up', kind: '', error: '' } }))
      .toMatchObject({ state: 'live', detail: 'via ssh' });
  });

  it('is down with a terse label and the hub\'s full sentence as the title', () => {
    const sentence = 'ssh key authentication to box was refused. The hub runs ssh with BatchMode...';
    const dot = hubDot({ reachable: false, detail: sentence, tunnel: { state: 'failed', kind: 'auth', error: sentence } });
    expect(dot).toEqual({ state: 'down', detail: 'ssh key refused', title: sentence });
  });

  it('says dubIS is not running when ssh works but the target does not answer', () => {
    const dot = hubDot({ reachable: false, detail: 'x', tunnel: { state: 'up', kind: 'remote_target', error: 'nothing is listening' } });
    expect(dot.detail).toBe('dubIS not running');
    expect(dot.title).toBe('nothing is listening');
  });
});
