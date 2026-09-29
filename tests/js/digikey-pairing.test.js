// @vitest-environment jsdom
/* digikey-pairing.test.js — the DOM half of the DigiKey sign-in panel: a code
   appears when you ask for one, the poll notices the extension's push, the
   code lapses honestly, and everything stops when the modal closes. */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const showToast = vi.fn();
vi.mock('../../js/ui-helpers.js', () => ({
  showToast: (...a) => showToast(...a),
  escHtml: (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
}));

const api = vi.fn();
vi.mock('../../js/api.js', () => ({
  setSourceHeaderProvider: () => {},
  api: (...a) => api(...a),
  AppLog: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));

const { wireDigikeyPanel, startDigikeyPanel, stopDigikeyPanel } =
  await import('../../js/digikey-pairing.js');

const $ = (id) => document.getElementById(id);
const flush = () => new Promise((r) => { setTimeout(r, 0); vi.advanceTimersByTime(0); });

/** Route api() calls by method name. */
function mockApi(handlers) {
  api.mockImplementation((method, ...args) => Promise.resolve(
    typeof handlers[method] === 'function' ? handlers[method](...args) : handlers[method],
  ));
}

beforeEach(() => {
  vi.useFakeTimers();
  document.body.innerHTML = `
    <span id="dk-status"></span>
    <button id="dk-login">Sign in to DigiKey</button>
    <button id="dk-logout" class="hidden">Logout</button>
    <div id="dk-pairing" class="hidden"></div>`;
  api.mockReset();
  showToast.mockReset();
  wireDigikeyPanel();
});

afterEach(() => {
  stopDigikeyPanel();
  vi.useRealTimers();
});

describe('opening the panel', () => {
  it('shows Sign in when not logged in', async () => {
    mockApi({ get_digikey_login_status: { logged_in: false } });
    await startDigikeyPanel();
    expect($('dk-status').textContent).toBe('Not logged in');
    expect($('dk-login').classList.contains('hidden')).toBe(false);
    expect($('dk-logout').classList.contains('hidden')).toBe(true);
    expect($('dk-pairing').classList.contains('hidden')).toBe(true);
  });

  it('shows Logout when logged in', async () => {
    mockApi({ get_digikey_login_status: { logged_in: true } });
    await startDigikeyPanel();
    expect($('dk-status').textContent).toBe('Logged in');
    expect($('dk-login').classList.contains('hidden')).toBe(true);
    expect($('dk-logout').classList.contains('hidden')).toBe(false);
  });
});

describe('signing in', () => {
  it('shows the code, a countdown and the instruction', async () => {
    mockApi({ get_digikey_login_status: { logged_in: false }, create_digikey_pairing: { nonce: 'DK-XYZ789', ttl: 600 } });
    await startDigikeyPanel();
    $('dk-login').click();
    await flush();
    expect(api).toHaveBeenCalledWith('create_digikey_pairing');
    expect($('dk-pairing').classList.contains('hidden')).toBe(false);
    expect($('dk-code').textContent).toBe('DK-XYZ789');
    expect($('dk-countdown').textContent).toContain('Good for 10:0');
    expect($('dk-pairing').textContent).toContain('digikey.com');
    expect($('dk-login').disabled).toBe(true);
  });

  it('polls every ~2 s and flips to Logged in when the push lands', async () => {
    let loggedIn = false;
    mockApi({
      get_digikey_login_status: () => ({ logged_in: loggedIn }),
      create_digikey_pairing: { nonce: 'DK-XYZ789', ttl: 600 },
    });
    await startDigikeyPanel();
    $('dk-login').click();
    await flush();
    const statusCalls = () => api.mock.calls.filter((c) => c[0] === 'get_digikey_login_status').length;
    const before = statusCalls();
    vi.advanceTimersByTime(2000);
    await flush();
    expect(statusCalls()).toBe(before + 1);
    expect($('dk-pairing').classList.contains('hidden')).toBe(false);

    loggedIn = true;
    vi.advanceTimersByTime(2000);
    await flush();
    expect($('dk-status').textContent).toBe('Logged in');
    expect($('dk-pairing').classList.contains('hidden')).toBe(true);
    expect($('dk-logout').classList.contains('hidden')).toBe(false);
    expect($('dk-login').classList.contains('hidden')).toBe(true);
    expect(showToast).toHaveBeenCalledWith('Signed in to DigiKey');

    // And the poll stopped.
    const after = statusCalls();
    vi.advanceTimersByTime(10000);
    await flush();
    expect(statusCalls()).toBe(after);
  });

  it('says the code expired and brings Sign in back', async () => {
    mockApi({ get_digikey_login_status: { logged_in: false }, create_digikey_pairing: { nonce: 'DK-XYZ789', ttl: 3 } });
    await startDigikeyPanel();
    $('dk-login').click();
    await flush();
    vi.advanceTimersByTime(4000);
    await flush();
    expect($('dk-countdown').textContent).toContain('expired');
    expect($('dk-countdown').textContent).toContain('Sign in to DigiKey');
    expect($('dk-code')).toBeNull();
    expect($('dk-login').disabled).toBe(false);
    expect($('dk-login').classList.contains('hidden')).toBe(false);
    expect($('dk-status').textContent).toBe('Pairing code expired');
  });

  it('toasts the reason when no code comes back', async () => {
    mockApi({ get_digikey_login_status: { logged_in: false }, create_digikey_pairing: undefined });
    await startDigikeyPanel();
    $('dk-login').click();
    await flush();
    expect(showToast).toHaveBeenCalledWith(expect.stringMatching(/pairing code/));
    expect($('dk-login').disabled).toBe(false);
    expect($('dk-pairing').classList.contains('hidden')).toBe(true);
  });
});

describe('closing the modal', () => {
  it('stops polling and drops the code', async () => {
    mockApi({ get_digikey_login_status: { logged_in: false }, create_digikey_pairing: { nonce: 'DK-XYZ789', ttl: 600 } });
    await startDigikeyPanel();
    $('dk-login').click();
    await flush();
    stopDigikeyPanel();
    expect($('dk-pairing').classList.contains('hidden')).toBe(true);
    const calls = api.mock.calls.length;
    vi.advanceTimersByTime(20000);
    await flush();
    expect(api.mock.calls.length).toBe(calls);
  });
});

describe('logging out', () => {
  it('calls logout_digikey and shows Sign in again', async () => {
    mockApi({ get_digikey_login_status: { logged_in: true }, logout_digikey: null });
    await startDigikeyPanel();
    $('dk-logout').click();
    await flush();
    expect(api).toHaveBeenCalledWith('logout_digikey');
    expect($('dk-status').textContent).toBe('Not logged in');
    expect($('dk-login').classList.contains('hidden')).toBe(false);
    expect($('dk-logout').classList.contains('hidden')).toBe(true);
    expect(showToast).toHaveBeenCalledWith('Digikey logged out');
  });
});
