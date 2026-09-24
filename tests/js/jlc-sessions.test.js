// @vitest-environment jsdom
/* jlc-sessions.test.js — the DOM half of the JLCPCB pairing panel.

   js/jlc-logic.js proves the projection drops a credential; this proves the
   panel renders through that projection, so nothing a payload carries can
   reach innerHTML (threat-model rule 4). The rest is the small amount of
   behaviour the binding owns: a code appears when you ask for one, Revoke
   names the account it is on, and the panel stops when the modal closes.
*/

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const showToast = vi.fn();
vi.mock('../../js/ui-helpers.js', () => ({
  showToast: (...a) => showToast(...a),
  // Real-enough escaping: the assertions below are about which *values* reach
  // the DOM, not about entity encoding.
  escHtml: (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
}));

const api = vi.fn();
vi.mock('../../js/api.js', () => ({
  setSourceHeaderProvider: () => {},
  api: (...a) => api(...a),
  AppLog: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));

const { wireJlcPanel, startJlcPanel, stopJlcPanel, renderJlcSessions } =
  await import('../../js/jlc-sessions.js');

const SECRET = 'SUPER-SECRET-COOKIE';

/** A sessions payload poisoned with everything a credential could hide in. */
function poisonedStatus() {
  return {
    logged_in: true,
    supported: true,
    message: '1 JLC account(s) paired',
    accounts: [{
      account: '12625901A',
      label: 'impossible_hardware',
      added_at: '2026-09-20T02:04:59Z',
      last_ok: '2026-09-20T02:05:03Z',
      cookies: [{ name: 'JLCPCB_SESSION_ID', value: SECRET, domain: '.jlcpcb.com' }],
      value: SECRET,
    }],
  };
}

beforeEach(() => {
  document.body.innerHTML = `
    <span id="jlc-status"></span>
    <button id="jlc-signin">Sign in to JLC</button>
    <div id="jlc-accounts"></div>
    <div id="jlc-pairing" class="hidden"></div>`;
  api.mockReset();
  showToast.mockReset();
  wireJlcPanel();
});

afterEach(() => {
  stopJlcPanel();
});

describe('rendering a paired account', () => {
  it('never puts a cookie value in the DOM, however the payload smuggles it', () => {
    renderJlcSessions(poisonedStatus());
    const html = document.body.innerHTML;
    expect(html).toContain('12625901A');
    expect(html).toContain('impossible_hardware');
    expect(html).not.toContain(SECRET);
    expect(html).not.toContain('JLCPCB_SESSION_ID');
    expect(html).not.toContain('jlcpcb.com');
  });

  it('shows the account, its label, both stamps and a Revoke button', () => {
    renderJlcSessions(poisonedStatus());
    const row = document.querySelector('.jlc-row');
    expect(row.dataset.account).toBe('12625901A');
    expect(row.textContent).toContain('added');
    expect(row.textContent).toContain('last ok');
    expect(row.querySelector('[data-act="revoke"]').textContent).toBe('Revoke');
    expect(document.getElementById('jlc-status').textContent).toBe('1 account paired');
  });

  it('says so, plainly, when nothing is paired', () => {
    renderJlcSessions({ supported: true, accounts: [] });
    expect(document.querySelector('.jlc-empty').textContent).toContain('No accounts paired');
    expect(document.getElementById('jlc-status').textContent).toBe('No JLC account paired');
  });
});

describe('opening the panel', () => {
  it('reads the session list and paints it', async () => {
    api.mockResolvedValue(poisonedStatus());
    await startJlcPanel();
    expect(api).toHaveBeenCalledWith('list_jlc_sessions');
    expect(document.querySelectorAll('.jlc-row')).toHaveLength(1);
    // No pairing code until the user asks for one.
    expect(document.getElementById('jlc-pairing').classList.contains('hidden')).toBe(true);
  });
});

describe('starting a sign-in', () => {
  it('shows the pairing code, a countdown and what to do with it', async () => {
    api.mockResolvedValue({ nonce: 'PAIR-CODE-123', ttl: 600 });
    document.getElementById('jlc-signin').click();
    await vi.waitFor(() => expect(document.getElementById('jlc-code')).toBeTruthy());

    expect(api).toHaveBeenCalledWith('create_jlc_pairing');
    const panel = document.getElementById('jlc-pairing');
    expect(panel.classList.contains('hidden')).toBe(false);
    expect(document.getElementById('jlc-code').textContent).toBe('PAIR-CODE-123');
    expect(document.getElementById('jlc-countdown').textContent).toContain('Good for 10:0');
    expect(panel.querySelectorAll('.jlc-steps li').length).toBeGreaterThan(2);
    // Says what to do, not what is true: dubIS cannot see the extension.
    expect(panel.textContent).toContain('cannot tell whether the extension is installed');
    expect(document.getElementById('jlc-signin').disabled).toBe(true);
  });

  it('re-enables the button and explains when no code comes back', async () => {
    // api() swallows a failed call and resolves undefined.
    api.mockResolvedValue(undefined);
    document.getElementById('jlc-signin').click();
    await vi.waitFor(() => expect(showToast).toHaveBeenCalled());
    expect(document.getElementById('jlc-signin').disabled).toBe(false);
    expect(showToast.mock.calls[0][0]).toContain('did not return a pairing code');
  });

  it('drops the code when the modal closes, rather than counting down unseen', async () => {
    api.mockResolvedValue({ nonce: 'PAIR-CODE-123', ttl: 600 });
    document.getElementById('jlc-signin').click();
    await vi.waitFor(() => expect(document.getElementById('jlc-code')).toBeTruthy());
    stopJlcPanel();
    expect(document.getElementById('jlc-pairing').innerHTML).toBe('');
    expect(document.getElementById('jlc-pairing').classList.contains('hidden')).toBe(true);
    expect(document.getElementById('jlc-signin').disabled).toBe(false);
  });
});

describe('revoking', () => {
  it('asks first, then revokes the account named on the row', async () => {
    renderJlcSessions(poisonedStatus());
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    api.mockResolvedValue({ account: '12625901A', revoked: true, accounts: [] });

    document.querySelector('[data-act="revoke"]').click();
    await vi.waitFor(() => expect(api).toHaveBeenCalledWith('revoke_jlc_session', '12625901A'));
    await vi.waitFor(() => expect(document.querySelector('.jlc-empty')).toBeTruthy());
    expect(showToast).toHaveBeenCalledWith('Revoked JLC session for 12625901A');
  });

  it('does nothing at all when the confirm is declined', () => {
    renderJlcSessions(poisonedStatus());
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    document.querySelector('[data-act="revoke"]').click();
    expect(api).not.toHaveBeenCalled();
    expect(document.querySelectorAll('.jlc-row')).toHaveLength(1);
  });
});
