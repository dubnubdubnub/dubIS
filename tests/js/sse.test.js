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
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  /** Fresh store.js + sse.js pair with a given INCLUDE_INVENTORY, wired to a fake EventSource. */
  async function setup(includeInventory) {
    vi.doMock('../../js/constants.js', constantsMock);
    const apiSpy = vi.fn().mockResolvedValue([{ part_key: 'A', qty: 1 }]);
    const AppLog = { info: vi.fn(), warn: vi.fn(), error: vi.fn(), clear: vi.fn() };
    vi.doMock('../../js/api.js', () => ({
      api: apiSpy,
      AppLog,
      INCLUDE_INVENTORY: includeInventory,
    }));
    const { connectEvents } = await import('../../js/sse.js');
    await import('../../js/store.js'); // side effect: registers the 'inventory.updated' handler
    connectEvents();
    const es = FakeEventSource.instances[0];
    return { apiSpy, AppLog, es };
  }

  it('INCLUDE_INVENTORY=true (current step-1 default): gate stays closed, no refresh call', async () => {
    const { apiSpy } = await setup(true);
    FakeEventSource.instances[0].emit('inventory.updated', { reason: 'adjust' });
    await vi.advanceTimersByTimeAsync(500);
    expect(apiSpy).not.toHaveBeenCalledWith('rebuild_inventory');
  });

  it('INCLUDE_INVENTORY=false: refreshes via rebuild_inventory after the debounce window', async () => {
    const { apiSpy, es } = await setup(false);
    es.emit('inventory.updated', { reason: 'adjust' });
    // Not yet — debounce hasn't elapsed.
    await vi.advanceTimersByTimeAsync(100);
    expect(apiSpy).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(200); // total 300ms > 250ms debounce
    expect(apiSpy).toHaveBeenCalledWith('rebuild_inventory');
    expect(apiSpy).toHaveBeenCalledTimes(1);
  });

  it('INCLUDE_INVENTORY=false: rapid-fire events collapse into a single trailing refresh', async () => {
    const { apiSpy, es } = await setup(false);
    es.emit('inventory.updated', { reason: 'a' });
    await vi.advanceTimersByTimeAsync(100);
    es.emit('inventory.updated', { reason: 'b' }); // resets the debounce timer
    await vi.advanceTimersByTimeAsync(100);
    es.emit('inventory.updated', { reason: 'c' }); // resets again
    expect(apiSpy).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(250);
    expect(apiSpy).toHaveBeenCalledTimes(1);
  });
});
