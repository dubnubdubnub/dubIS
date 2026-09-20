/* jlc-logic.test.js — the pure half of the JLCPCB pairing panel.

   The load-bearing assertions here are the two threat-model rules the UI is
   responsible for (docs/plans/2026-09-20-extension-credential-capture.md):

     rule 4  no rendered string can contain a credential
     honesty the panel never claims a state it cannot verify (whether the
             browser extension is installed)
*/

import { describe, it, expect } from 'vitest';
import {
  relativeAge,
  sessionRow,
  sessionRows,
  statusLine,
  pairingFromResponse,
  countdown,
  newAccounts,
  PAIRING_TTL_SECONDS,
  PUBLIC_SESSION_FIELDS,
  PAIRING_STEPS,
  EXTENSION_UNVERIFIABLE,
} from '../../js/jlc-logic.js';

const NOW = Date.parse('2026-09-20T12:00:00Z');

function account(extra = {}) {
  return {
    account: '12625901A',
    label: 'impossible_hardware',
    added_at: '2026-09-20T02:04:59Z',
    last_ok: '2026-09-20T11:59:30Z',
    ...extra,
  };
}

// ── Rule 4: write-only, so nothing renderable may carry a credential ────────

describe('no rendered value can be a credential', () => {
  it('drops every field that is not one of the four public ones', () => {
    const row = sessionRow(account({
      cookies: [{ name: 'JLCPCB_SESSION_ID', value: 'SUPER-SECRET-COOKIE' }],
      value: 'SUPER-SECRET-COOKIE',
      token: 'SUPER-SECRET-COOKIE',
    }), NOW);
    expect(JSON.stringify(row)).not.toContain('SUPER-SECRET-COOKIE');
    expect(JSON.stringify(row)).not.toContain('JLCPCB_SESSION_ID');
  });

  it('keeps the whitelist and the projection in agreement', () => {
    // If a fifth public field is ever added, this fails until sessionRow is
    // taught about it — the whitelist cannot silently drift into a denylist.
    const row = sessionRow(account(), NOW);
    expect(PUBLIC_SESSION_FIELDS).toEqual(['account', 'label', 'added_at', 'last_ok']);
    expect(Object.keys(row).sort()).toEqual(
      ['account', 'addedAgo', 'addedAt', 'label', 'lastOk', 'lastOkAgo'],
    );
  });

  it('survives a payload that is a list of cookie objects rather than sessions', () => {
    const rows = sessionRows(
      [{ name: 'JLCPCB_SESSION_ID', value: 'SUPER-SECRET-COOKIE' }],
      NOW,
    );
    // No `account`, so there is nothing to render and nothing to revoke.
    expect(rows).toEqual([]);
  });
});

// ── Session rows ────────────────────────────────────────────────────────────

describe('sessionRows', () => {
  it('projects a stored session into the fields the panel shows', () => {
    const [row] = sessionRows([account()], NOW);
    expect(row.account).toBe('12625901A');
    expect(row.label).toBe('impossible_hardware');
    expect(row.lastOkAgo).toBe('just now');
    expect(row.addedAgo).toBe('9 h ago');
  });

  it('drops an entry with no account rather than rendering a nameless Revoke', () => {
    expect(sessionRows([account(), { label: 'orphan' }], NOW)).toHaveLength(1);
  });

  it('answers [] for anything that is not an array', () => {
    for (const bad of [undefined, null, {}, 'nope', 7]) {
      expect(sessionRows(bad, NOW)).toEqual([]);
    }
  });
});

describe('relativeAge', () => {
  it('reads an empty stamp as never, not as the epoch', () => {
    expect(relativeAge('', NOW)).toBe('never');
    expect(relativeAge(null, NOW)).toBe('never');
  });

  it('reads an unparseable stamp as unknown', () => {
    expect(relativeAge('last Tuesday', NOW)).toBe('unknown');
  });

  it('bands by minute, hour and day', () => {
    expect(relativeAge('2026-09-20T11:59:59Z', NOW)).toBe('just now');
    expect(relativeAge('2026-09-20T11:45:00Z', NOW)).toBe('15 min ago');
    expect(relativeAge('2026-09-20T09:00:00Z', NOW)).toBe('3 h ago');
    expect(relativeAge('2026-09-17T12:00:00Z', NOW)).toBe('3 d ago');
  });

  it('reads a future stamp as just now rather than a negative age', () => {
    expect(relativeAge('2026-09-21T12:00:00Z', NOW)).toBe('just now');
  });
});

// ── Honest status ───────────────────────────────────────────────────────────

describe('statusLine', () => {
  it('says it is checking before the first answer arrives', () => {
    expect(statusLine(null)).toBe('Checking…');
  });

  it('reports the count the credential store actually holds', () => {
    expect(statusLine({ supported: true, accounts: [] })).toBe('No JLC account paired');
    expect(statusLine({ supported: true, accounts: [account()] })).toBe('1 account paired');
    expect(statusLine({
      supported: true,
      accounts: [account(), account({ account: 'B2' })],
    })).toBe('2 accounts paired');
  });

  it('defers to the server when the feature is unsupported there', () => {
    expect(statusLine({ supported: false, message: 'no credential store', accounts: [] }))
      .toBe('no credential store');
  });

  it('never claims anything about the browser extension', () => {
    // dubIS cannot see the browser: the extension pushes to dubIS and dubIS
    // never calls it, so "installed" and "installed but not clicked" are
    // indistinguishable from here. The panel must say what to DO instead.
    for (const status of [null, { supported: true, accounts: [] }, { supported: true, accounts: [account()] }]) {
      expect(statusLine(status).toLowerCase()).not.toContain('extension');
      expect(statusLine(status).toLowerCase()).not.toContain('install');
    }
    expect(EXTENSION_UNVERIFIABLE).toContain('cannot tell whether the extension is installed');
    expect(PAIRING_STEPS.every((s) => typeof s === 'string' && s.length > 0)).toBe(true);
  });
});

// ── Pairing ─────────────────────────────────────────────────────────────────

describe('pairingFromResponse', () => {
  it('takes the nonce and the server-stated TTL', () => {
    const p = pairingFromResponse({ nonce: 'abc123', ttl: 600 }, NOW);
    expect(p).toMatchObject({ ok: true, code: 'abc123', ttl: 600 });
    expect(p.expiresAtMs).toBe(NOW + 600000);
  });

  it('falls back to the documented TTL when the server omits one', () => {
    expect(pairingFromResponse({ nonce: 'abc123' }, NOW).ttl).toBe(PAIRING_TTL_SECONDS);
    expect(pairingFromResponse({ nonce: 'abc123', ttl: 0 }, NOW).ttl).toBe(PAIRING_TTL_SECONDS);
    expect(pairingFromResponse({ nonce: 'abc123', ttl: 'soon' }, NOW).ttl).toBe(PAIRING_TTL_SECONDS);
  });

  it('refuses a response with no nonce — api() resolves undefined on failure', () => {
    for (const bad of [undefined, null, {}, { nonce: '  ' }]) {
      const p = pairingFromResponse(bad, NOW);
      expect(p.ok).toBe(false);
      expect(p.code).toBe('');
      expect(p.reason).toContain('did not return a pairing code');
    }
  });
});

describe('countdown', () => {
  it('counts down in m:ss', () => {
    expect(countdown(NOW + 600000, NOW).text).toBe('Good for 10:00');
    expect(countdown(NOW + 65000, NOW).text).toBe('Good for 1:05');
    expect(countdown(NOW + 9000, NOW).text).toBe('Good for 0:09');
  });

  it('says a lapsed code is dead and how to get another', () => {
    const state = countdown(NOW - 1, NOW);
    expect(state.expired).toBe(true);
    expect(state.secondsLeft).toBe(0);
    expect(state.text).toContain('expired');
    expect(state.text).toContain('Sign in to JLC');
  });
});

describe('newAccounts', () => {
  it('names the accounts that appeared since the snapshot', () => {
    const rows = sessionRows([account(), account({ account: 'B2' })], NOW);
    expect(newAccounts(rows, new Set(['12625901A']))).toEqual(['B2']);
    expect(newAccounts(rows, new Set(['12625901A', 'B2']))).toEqual([]);
    expect(newAccounts([], new Set(['12625901A']))).toEqual([]);
  });
});
