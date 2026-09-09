// tests/js/vendor-picker-blur-race.test.mjs
// @vitest-environment jsdom
//
// Guards the fix for the mfg-direct "favicon never appears" flake
// (tests/js/e2e/mfg-direct.spec.mjs). Both vendor blur handlers are wired as
// fire-and-forget `onblur` callbacks, so tabbing name -> url -> out starts the
// url blur while the name upsert is still in flight. If they are allowed to
// overlap, the url blur sees an id-less vendor, stashes the URL locally, and
// the resolving name upsert overwrites that stash — the typed URL is lost and
// no request ever carries it, so the favicon never arrives.
import { describe, it, expect, vi } from 'vitest';

const upsert = vi.fn();
vi.mock('../../js/api.js', () => ({ apiVendors: { upsert: (...a) => upsert(...a) } }));
vi.mock('../../js/store.js', () => ({ store: { vendors: [] } }));

const { createVendorPicker } = await import('../../js/import/mfg-direct/vendor-picker.js');

/** A promise plus its resolver, so a test can hold a response open. */
function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

describe('createVendorPicker overlapping blurs', () => {
  it('carries the URL typed while the name upsert is still in flight', async () => {
    const nameCall = deferred();
    upsert.mockReset();
    upsert
      // 1st: the name blur — held open until we release it.
      .mockImplementationOnce(() => nameCall.promise)
      // 2nd: whatever the url blur decides to send.
      .mockImplementationOnce((id, _name, url) =>
        Promise.resolve({ id, name: 'TMR Sensors', url, favicon_path: 'f.ico', type: 'real' }));

    let vendor = { id: '', name: '', url: '', favicon_path: '', icon: '', type: '' };
    const picker = createVendorPicker({
      getVendor: () => vendor,
      setVendor: (v) => { vendor = v; },
    });

    const namePending = picker.onVendorNameBlur('TMR Sensors');
    // The user has already tabbed on and typed a website by the time the name
    // upsert answers.
    const urlPending = picker.onVendorUrlBlur('tmr-sensors.com');
    nameCall.resolve({ id: 'v_tmr', name: 'TMR Sensors', url: '', favicon_path: '', type: 'inferred' });
    await namePending;
    await urlPending;

    expect(upsert).toHaveBeenCalledTimes(2);
    expect(upsert.mock.calls[1]).toEqual(['v_tmr', '', 'https://tmr-sensors.com']);
    expect(vendor.url).toBe('https://tmr-sensors.com');
    expect(vendor.favicon_path).toBe('f.ico');
  });
});
