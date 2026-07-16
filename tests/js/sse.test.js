// @vitest-environment jsdom
//
// js/sse.js: thin EventSource wrapper (dispatch + JSON parsing), plus the
// store.js gating/debounce wiring built on top of it (Task 3 of
// docs/plans/2026-07-16-phase1b-frontend-port-plan.md).
//
// jsdom has no EventSource implementation, so every test installs a minimal
// fake before importing sse.js. Module state in sse.js (the open connection,
// registered handlers) is a singleton, so tests that need isolation call
// vi.resetModules() and re-import.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

class FakeEventSource {
  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this._listeners = {};
    FakeEventSource.instances.push(this);
  }
  addEventListener(name, fn) {
    (this._listeners[name] = this._listeners[name] || []).push(fn);
  }
  removeEventListener(name, fn) {
    if (this._listeners[name]) {
      this._listeners[name] = this._listeners[name].filter((f) => f !== fn);
    }
  }
  close() { this.readyState = 2; }
  /** Test helper: simulate the server pushing a named event. */
  emit(name, data) {
    (this._listeners[name] || []).forEach((fn) => fn({ data: JSON.stringify(data) }));
  }
}
FakeEventSource.instances = [];

beforeEach(() => {
  FakeEventSource.instances = [];
  global.EventSource = FakeEventSource;
});

afterEach(() => {
  delete global.EventSource;
});

describe('sse.js: connectEvents/onEvent', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it('opens an EventSource at baseUrl + /v1/events (default baseUrl "")', async () => {
    const { connectEvents } = await import('../../js/sse.js');
    connectEvents();
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.instances[0].url).toBe('/v1/events');
  });

  it('honors a custom baseUrl', async () => {
    const { connectEvents } = await import('../../js/sse.js');
    connectEvents('http://127.0.0.1:9000');
    expect(FakeEventSource.instances[0].url).toBe('http://127.0.0.1:9000/v1/events');
  });

  it('connectEvents is idempotent: a second call reuses the existing connection', async () => {
    const { connectEvents } = await import('../../js/sse.js');
    const first = connectEvents();
    const second = connectEvents();
    expect(second).toBe(first);
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it('dispatches a registered handler with the JSON-parsed payload', async () => {
    const { connectEvents, onEvent } = await import('../../js/sse.js');
    const handler = vi.fn();
    onEvent('scan.received', handler);
    connectEvents();
    FakeEventSource.instances[0].emit('scan.received', { filename: 'a.jpg', count: 1 });
    expect(handler).toHaveBeenCalledWith({ filename: 'a.jpg', count: 1 });
  });

  it('a handler registered AFTER connectEvents() still receives events', async () => {
    const { connectEvents, onEvent } = await import('../../js/sse.js');
    connectEvents();
    const handler = vi.fn();
    onEvent('inventory.consumed', handler);
    FakeEventSource.instances[0].emit('inventory.consumed', { part_key: 'X', qty: 2 });
    expect(handler).toHaveBeenCalledWith({ part_key: 'X', qty: 2 });
  });

  it('supports multiple handlers on the same event name', async () => {
    const { connectEvents, onEvent } = await import('../../js/sse.js');
    const a = vi.fn();
    const b = vi.fn();
    onEvent('inventory.updated', a);
    onEvent('inventory.updated', b);
    connectEvents();
    FakeEventSource.instances[0].emit('inventory.updated', { reason: 'adjust' });
    expect(a).toHaveBeenCalledWith({ reason: 'adjust' });
    expect(b).toHaveBeenCalledWith({ reason: 'adjust' });
  });

  it('a handler for a different event name is not invoked', async () => {
    const { connectEvents, onEvent } = await import('../../js/sse.js');
    const receivingHandler = vi.fn();
    const receivedHandler = vi.fn();
    onEvent('scan.receiving', receivingHandler);
    onEvent('scan.received', receivedHandler);
    connectEvents();
    FakeEventSource.instances[0].emit('scan.received', { line_items: [] });
    expect(receivedHandler).toHaveBeenCalledTimes(1);
    expect(receivingHandler).not.toHaveBeenCalled();
  });
});

describe('store.js: inventory.updated wiring (gating + debounce)', () => {
  const constantsMock = () => ({
    SECTION_ORDER: ['Resistors'],
    FIELDNAMES: [],
  });

  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
    // updateInventoryHeader() (called by onInventoryUpdated, invoked at the
    // end of the debounced refresh chain) writes into these two elements —
    // stub them so awaiting scheduleInventoryRefresh() to full completion
    // doesn't crash on a null getElementById in this DOM-less test file.
    document.body.innerHTML = '<span id="inv-count"></span><span id="inv-total-value"></span>';
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  /** Fresh store.js + sse.js pair, wired to a fake EventSource. */
  async function setup() {
    vi.doMock('../../js/constants.js', constantsMock);
    const apiSpy = vi.fn().mockResolvedValue([{ part_key: 'A', qty: 1 }]);
    const AppLog = { info: vi.fn(), warn: vi.fn(), error: vi.fn(), clear: vi.fn() };
    vi.doMock('../../js/api.js', () => ({
      api: apiSpy,
      AppLog,
    }));
    const { connectEvents } = await import('../../js/sse.js');
    const store = await import('../../js/store.js'); // side effect: registers the 'inventory.updated' handler
    connectEvents();
    const es = FakeEventSource.instances[0];
    // `onInventoryUpdated` (invoked at the end of the refresh chain) also
    // cascades into `loadVendorsAndPOs()` (list_vendors + list_purchase_orders),
    // all through this same apiSpy — count only the rebuild_inventory calls.
    const rebuildCalls = () => apiSpy.mock.calls.filter(([m]) => m === 'rebuild_inventory');
    return { apiSpy, rebuildCalls, AppLog, es, store };
  }

  // Task 10: SSE is the sole gate-free re-render source — there is no
  // INCLUDE_INVENTORY flag anymore, so every 'inventory.updated' push always
  // schedules a debounced rebuild_inventory refetch.
  it('refreshes via rebuild_inventory after the debounce window', async () => {
    const { apiSpy, rebuildCalls, es } = await setup();
    es.emit('inventory.updated', { reason: 'adjust' });
    // Not yet — debounce hasn't elapsed.
    await vi.advanceTimersByTimeAsync(100);
    expect(apiSpy).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(200); // total 300ms > 250ms debounce
    expect(rebuildCalls()).toHaveLength(1);
  });

  it('rapid-fire events collapse into a single trailing refresh', async () => {
    const { apiSpy, rebuildCalls, es } = await setup();
    es.emit('inventory.updated', { reason: 'a' });
    await vi.advanceTimersByTimeAsync(100);
    es.emit('inventory.updated', { reason: 'b' }); // resets the debounce timer
    await vi.advanceTimersByTimeAsync(100);
    es.emit('inventory.updated', { reason: 'c' }); // resets again
    expect(apiSpy).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(250);
    expect(rebuildCalls()).toHaveLength(1);
  });

  it('scheduleInventoryRefresh() (direct post-mutation call) shares the debounce with SSE', async () => {
    const { rebuildCalls, es, store } = await setup();
    // A mutation call site fires a direct refresh...
    const p = store.scheduleInventoryRefresh();
    await vi.advanceTimersByTimeAsync(100);
    // ...then the mutation's own SSE echo arrives before the debounce fires —
    // it must NOT restart a second independent refresh cycle beyond the
    // shared timer resetting (still just one eventual rebuild_inventory call).
    es.emit('inventory.updated', { reason: 'adjust' });
    await vi.advanceTimersByTimeAsync(250);
    await p;
    expect(rebuildCalls()).toHaveLength(1);
  });
});
