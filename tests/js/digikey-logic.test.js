/* digikey-logic.test.js — the pure half of the DigiKey sign-in panel and the
   DigiKey hover-preview error line. */

import { describe, it, expect } from 'vitest';
import {
  dkCountdown,
  loginStatusView,
  digikeyPreviewError,
  pairingFromResponse,
  DK_SIGNIN_LABEL,
  DK_PAIRING_INSTRUCTION,
  DK_POLL_MS,
} from '../../js/digikey-logic.js';

const NOW = Date.parse('2026-09-29T12:00:00Z');

describe('dkCountdown', () => {
  it('counts down in m:ss', () => {
    expect(dkCountdown(NOW + 600000, NOW).text).toBe('Good for 10:00');
    expect(dkCountdown(NOW + 65000, NOW).expired).toBe(false);
  });

  it('points an expired code at the DigiKey button, not the JLC one', () => {
    const state = dkCountdown(NOW, NOW);
    expect(state.expired).toBe(true);
    expect(state.text).toContain('expired');
    expect(state.text).toContain(DK_SIGNIN_LABEL);
    expect(state.text).not.toContain('JLC');
  });
});

describe('pairingFromResponse (shared with JLC)', () => {
  it('reads a DK- nonce and the server TTL', () => {
    const minted = pairingFromResponse({ nonce: 'DK-ABC123', ttl: 600 }, NOW);
    expect(minted.ok).toBe(true);
    expect(minted.code).toBe('DK-ABC123');
    expect(minted.expiresAtMs).toBe(NOW + 600000);
  });

  it('says why when the call failed', () => {
    const minted = pairingFromResponse(undefined, NOW);
    expect(minted.ok).toBe(false);
    expect(minted.reason).toMatch(/pairing code/);
  });
});

describe('loginStatusView', () => {
  it('logged in', () => {
    const v = loginStatusView({ logged_in: true });
    expect(v).toMatchObject({ loggedIn: true, known: true, text: 'Logged in' });
  });

  it('not logged in', () => {
    const v = loginStatusView({ logged_in: false });
    expect(v).toMatchObject({ loggedIn: false, known: true, text: 'Not logged in' });
  });

  it('a failed status read is not reported as "not logged in"', () => {
    const v = loginStatusView(undefined);
    expect(v.known).toBe(false);
    expect(v.loggedIn).toBe(false);
    expect(v.text).not.toBe('Not logged in');
  });
});

describe('digikeyPreviewError', () => {
  it('keeps the plain message when logged in', () => {
    expect(digikeyPreviewError({ logged_in: true })).toBe('Could not load product data');
  });

  it('suggests, rather than requires, signing in when not logged in', () => {
    const msg = digikeyPreviewError({ logged_in: false });
    expect(msg).toBe('Could not load product data — signing in to DigiKey in Preferences may help');
    expect(msg).not.toMatch(/to enable preview/);
    expect(digikeyPreviewError(undefined)).toBe(msg);
  });
});

describe('copy', () => {
  it('the instruction says where to sign in and what to do with the code', () => {
    expect(DK_PAIRING_INSTRUCTION).toContain('digikey.com');
    expect(DK_PAIRING_INSTRUCTION).toContain('bridge extension');
    expect(DK_PAIRING_INSTRUCTION).toContain('Send');
  });

  it('polls about every 2 s', () => {
    expect(DK_POLL_MS).toBe(2000);
  });
});
