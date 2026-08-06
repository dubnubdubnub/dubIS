// @vitest-environment jsdom
/* CI guard for a whole CLASS of bug: a *passive* interaction reopening a panel
 * the user deliberately collapsed.
 *
 * The reported instance: hovering an LCSC part number to read its tooltip made
 * the collapsed Purchase Import panel pop open. Nothing about a hover changes a
 * purchase order — but the chain got there anyway:
 *
 *   hover [data-lcsc]  (js/part-preview.js)
 *     -> api('record_fetched_prices')            a real mutation, so the server
 *     -> SSE `inventory.updated`                 publishes to every client
 *     -> scheduleInventoryRefresh()              (js/store.js)
 *     -> onInventoryUpdated() -> loadVendorsAndPOs()
 *     -> setPurchaseOrders(<identical list>)     re-fetch, byte-identical data
 *     -> EventBus PO_CHANGED                     ...but announced as a CHANGE
 *     -> handleTrigger('PO_CHANGED')             (js/app-init.js)
 *     -> the collapsed import panel is forced open
 *
 * The defect is the second-to-last step: a `*_CHANGED` event fired by a refresh
 * that changed nothing. `onInventoryUpdated` re-fetches vendors/POs after EVERY
 * inventory mutation — including the many that cannot touch them — so any such
 * event is a reopen trigger waiting to happen.
 *
 * These tests therefore guard the class, not the trigger:
 *   1. every entry in REOPEN_TRIGGERS must be classified as either refresh-fed
 *      data (and then behaviourally proven change-only) or a genuine one-shot
 *      user gesture. A new trigger fails this until you classify it.
 *   2. the real chain: a no-op inventory refresh must emit nothing at all.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../js/ui-helpers.js', () => ({
  showToast: vi.fn(), escHtml: vi.fn(s => s || ''), Modal: vi.fn(),
  formatMoney: vi.fn(n => '$' + Number(n || 0).toFixed(2)),
}));
vi.mock('../../js/constants.js', () => ({ SECTION_ORDER: [], FIELDNAMES: [] }));
vi.mock('../../js/api.js', () => ({
  api: vi.fn().mockResolvedValue(undefined),
  AppLog: { info: vi.fn(), warn: vi.fn(), error: vi.fn(), clear: vi.fn() },
}));

import {
  store, setVendors, setPurchaseOrders, onInventoryUpdated,
} from '../../js/store.js';
import { EventBus, Events } from '../../js/event-bus.js';
import { api } from '../../js/api.js';
import { REOPEN_TRIGGERS } from '../../js/panel-collapse-logic.js';

const VENDORS = [{ id: 'v_lcsc', name: 'LCSC', type: 'real', icon: '', url: 'https://lcsc.com', favicon_path: '' }];
const POS = [{ po_id: 'po_1', vendor_id: 'v_lcsc', purchase_date: '2026-05-31' }];

/** Fresh-but-equal data: proves the compare is by value, not by reference. */
const clone = (v) => JSON.parse(JSON.stringify(v));

// ── 1. Every reopen trigger must be classified ────────────────────────────────

/* Triggers fed by data the app re-fetches on a schedule / after unrelated
   mutations. These are only trustworthy as reopen triggers if they fire on
   genuine change, so each names the setter that raises it and gets verified
   below. */
const REFRESH_FED = {
  PO_CHANGED: {
    event: Events.PO_CHANGED,
    set: (data) => setPurchaseOrders(data),
    read: () => store.purchaseOrders,
    sample: POS,
    /** The same data plus one genuinely new row. */
    changed: () => [...clone(POS), { po_id: 'po_2', vendor_id: 'v_lcsc', purchase_date: '2026-06-01' }],
  },
};

/* One-shot triggers: each is raised by a discrete user gesture or a genuine
   state transition, never by a background re-fetch, so "did the data change?"
   does not apply. Moving a trigger here to silence the guard defeats it —
   the test for it is that nothing re-raises it on a refresh. */
const ONE_SHOT = {
  IMPORT_MAPPER_OPENED: 'user opened the column mapper',
  CART_ADD_MODE: 'user armed cart-add mode',
  LABEL_MODE: 'user toggled Print Labels mode',
  BOM_LOADED: 'user loaded a BOM file',
  BOM_CLEARED: 'user cleared the BOM',
  CONFIRMED_CHANGED: 'user confirmed/unconfirmed a match',
  LINKS_CHANGED: 'user made or undid a manual link',
  LINKING_MODE: 'user armed linking mode',
  LOG_WARN: 'a warning was logged — surfacing it is the documented intent',
  LOG_ERROR: 'an error was logged — surfacing it is the documented intent',
};

describe('reopen triggers are classified', () => {
  it('every trigger is either refresh-fed or one-shot', () => {
    for (const name of Object.keys(REOPEN_TRIGGERS)) {
      const classified = name in REFRESH_FED || name in ONE_SHOT;
      expect(classified,
        `${name} is a new reopen trigger. Classify it: if the app re-fetches its `
        + 'data (so the event can fire with unchanged data), add it to REFRESH_FED '
        + 'with a setter + sample and it will be proven change-only; if a discrete '
        + 'user gesture raises it, add it to ONE_SHOT with the gesture.').toBe(true);
    }
  });

  it('no stale classifications', () => {
    for (const name of [...Object.keys(REFRESH_FED), ...Object.keys(ONE_SHOT)]) {
      expect(REOPEN_TRIGGERS[name], `${name} is classified but no longer a reopen trigger`)
        .toBeDefined();
    }
  });
});

// ── 2. Refresh-fed triggers must be change-only ───────────────────────────────

describe.each(Object.entries(REFRESH_FED))('%s fires only on real change', (name, spec) => {
  /** @type {import('vitest').Mock} */
  let spy;

  beforeEach(() => {
    store.vendors = [];
    store.purchaseOrders = [];
    spy = vi.fn();
    EventBus.on(spec.event, spy);
  });

  it('fires when the data first arrives', () => {
    spec.set(clone(spec.sample));
    expect(spy).toHaveBeenCalledTimes(1);
    EventBus.off(spec.event, spy);
  });

  it('does NOT fire again when a re-fetch returns equal data', () => {
    spec.set(clone(spec.sample));
    spec.set(clone(spec.sample));
    spec.set(clone(spec.sample));
    expect(spy, `${name} announced a change three times for one change — a passive `
      + 'refresh would reopen the collapsed panel it feeds').toHaveBeenCalledTimes(1);
    EventBus.off(spec.event, spy);
  });

  it('fires again once the data really changes', () => {
    spec.set(clone(spec.sample));
    spec.set(spec.changed());
    expect(spy, 'a genuine change must still reopen the panel').toHaveBeenCalledTimes(2);
    EventBus.off(spec.event, spy);
  });

  it('still updates the store even when it stays silent', () => {
    spec.set(clone(spec.sample));
    const fresh = clone(spec.sample);
    spec.set(fresh);
    // Silence must mean "no event", never "no write" — a stale store would be a
    // far worse bug than a spurious reopen.
    expect(spec.read()).toBe(fresh);
    EventBus.off(spec.event, spy);
  });
});

// ── 3. The real chain: a no-op refresh must be silent end to end ──────────────

describe('a no-op inventory refresh reopens nothing', () => {
  beforeEach(() => {
    document.body.innerHTML = '<span id="inv-count"></span><span id="inv-total-value"></span>';
    store.vendors = [];
    store.purchaseOrders = [];
    api.mockReset();
    // Whatever the mutation was, the vendor/PO lists come back unchanged —
    // exactly what a hover-triggered record_fetched_prices produces.
    api.mockImplementation((method) => {
      if (method === 'list_vendors') return Promise.resolve(clone(VENDORS));
      if (method === 'list_purchase_orders') return Promise.resolve(clone(POS));
      return Promise.resolve(undefined);
    });
  });

  it('emits no PO_CHANGED / VENDORS_CHANGED after the data has settled', async () => {
    // Settle: first load legitimately announces both.
    setVendors(clone(VENDORS));
    setPurchaseOrders(clone(POS));

    const po = vi.fn();
    const vend = vi.fn();
    EventBus.on(Events.PO_CHANGED, po);
    EventBus.on(Events.VENDORS_CHANGED, vend);

    // The tail of the hover chain: an SSE-driven refresh for a mutation that
    // touched neither vendors nor POs.
    onInventoryUpdated([{ lcsc: 'C1', qty: 1, unit_price: 0 }]);
    await vi.waitFor(() => expect(api).toHaveBeenCalledWith('list_purchase_orders'));
    await Promise.resolve();

    expect(po, 'a refresh that changed no PO must not announce a PO change — '
      + 'panel-collapse.js answers PO_CHANGED by force-opening the import panel')
      .not.toHaveBeenCalled();
    expect(vend).not.toHaveBeenCalled();

    EventBus.off(Events.PO_CHANGED, po);
    EventBus.off(Events.VENDORS_CHANGED, vend);
  });
});
