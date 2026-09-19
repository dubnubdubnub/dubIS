// @ts-check
/* signals.js — Tiny reactive signals primitive (~80 LOC).
   API: signal(v), computed(fn), effect(fn), batch(fn)
   No build step, no dependencies. */

// ── Observer tracking ─────────────────────────────────────
/** @type {Obs|null} */
let _currentObs = null;

/** @type {Set<Obs>|null} — queued notifications during a batch */
let _batchQueue = null;

/**
 * Internal observer handle used by effect (and computed via effect).
 * @typedef {{ run(): void, dispose(): void, disposed: boolean, _addDep(unsub: (obs: Obs) => void): void }} Obs
 */

// ── signal ────────────────────────────────────────────────

/**
 * Create a writable reactive signal.
 * @template T
 * @param {T} initialValue
 * @returns {{ get(): T, set(v: T): void, peek(): T }}
 */
export function signal(initialValue) {
  let _value = initialValue;
  /** @type {Set<Obs>} */
  const _subs = new Set();

  /** Called by effect._cleanup to unsubscribe. */
  function _unsub(obs) { _subs.delete(obs); }

  return {
    get() {
      if (_currentObs && !_currentObs.disposed) {
        _subs.add(_currentObs);
        _currentObs._addDep(_unsub);
      }
      return _value;
    },
    set(v) {
      if (_value === v) return;
      _value = v;
      if (_batchQueue) {
        _subs.forEach(s => _batchQueue.add(s));
      } else {
        [..._subs].forEach(s => { if (!s.disposed) s.run(); });
      }
    },
    peek() { return _value; },
  };
}

// ── effect ────────────────────────────────────────────────

/**
 * Run `fn` immediately; re-run whenever any read signal changes.
 * Returns a dispose function.
 * @param {() => void} fn
 * @returns {() => void}
 */
export function effect(fn) {
  /** @type {Array<(obs: Obs) => void>} unsubscribe callbacks for each dep */
  let _depUnsubs = [];
  let _running = false;

  let _pendingRun = false;

  /** @type {Obs} */
  const obs = {
    disposed: false,
    run() {
      if (obs.disposed) return;
      if (_running) { _pendingRun = true; return; }
      // Unsubscribe from old deps; re-run to collect new ones
      _depUnsubs.forEach(unsub => unsub(obs));
      _depUnsubs = [];
      const prev = _currentObs;
      _currentObs = obs;
      _running = true;
      try { fn(); } finally {
        _running = false;
        _currentObs = prev;
        if (_pendingRun) { _pendingRun = false; obs.run(); }
      }
    },
    dispose() {
      obs.disposed = true;
      _depUnsubs.forEach(unsub => unsub(obs));
      _depUnsubs = [];
    },
    _addDep(unsub) { _depUnsubs.push(unsub); },
  };

  obs.run();

  return () => obs.dispose();
}

// ── computed ──────────────────────────────────────────────

/**
 * Lazily-evaluated derived signal. Recomputes when dependencies change.
 * @template T
 * @param {() => T} fn
 * @returns {{ get(): T, peek(): T }}
 */
export function computed(fn) {
  const _sig = signal(/** @type {T} */ (undefined));
  effect(() => { _sig.set(fn()); });
  return {
    get() { return _sig.get(); },
    peek() { return _sig.peek(); },
  };
}

// ── batch ─────────────────────────────────────────────────

/**
 * Coalesce all signal `.set()` calls inside `fn` into one notification pass.
 * @param {() => void} fn
 */
export function batch(fn) {
  if (_batchQueue) { fn(); return; } // already batching
  _batchQueue = new Set();
  try { fn(); } finally {
    const queue = _batchQueue;
    _batchQueue = null;
    queue.forEach(s => { if (!s.disposed) s.run(); });
  }
}

// ── App-level signal instances ───────────────────────────────
//
// Cross-panel *state* (as opposed to discrete UI *events*, which stay on
// EventBus) propagates via signal instances. `cartsSignal` mirrors carts +
// the active cart id; `js/cart/cart-store.js` owns writes to it, panels
// read it via `.get()`/`effect()`.

/** @typedef {{ carts: Array<Object>, activeCartId: string|null }} CartsState */

/** @type {{ get(): CartsState, set(v: CartsState): void, peek(): CartsState }} */
export const cartsSignal = signal(/** @type {CartsState} */ ({ carts: [], activeCartId: null }));

// The quick-switcher's tabs, which one is in front, and the source roster they
// are built from. State, not an event: two panels read it at any moment (the
// tab strip in js/server-tabs.js and the roster in js/server-list.js) and both
// must agree at all times, including for a panel that mounts *after* the switch
// happened — which is exactly what an EventBus message cannot do, since a
// listener that was not subscribed when it fired never learns it fired.
// `js/store.js` owns every write.

/**
 * @typedef {{id: string, name: string, url: string, enabled: boolean,
 *            reachable: (boolean|undefined), detail: string}} SourceEntry
 */
/**
 * A quick-switcher tab: a view INSTANCE, not a server. `sources` holds one id
 * for a plain tab and several for a group, `view` is a `captureView()` snapshot
 * from js/inventory/saved-views.js, and `id` is the tab's own — the same server
 * can be open more than once, which is the point of the per-tab view.
 * @typedef {{id: string, sources: string[], name: string, view: (object|null)}} TabEntry
 */
/** @typedef {{ tabs: Array<TabEntry>, activeTabId: string, sources: Array<SourceEntry> }} ActiveSourceState */

/** @type {{ get(): ActiveSourceState, set(v: ActiveSourceState): void, peek(): ActiveSourceState }} */
export const activeSourceSignal = signal(
  /** @type {ActiveSourceState} */ ({ tabs: [], activeTabId: '', sources: [] }),
);

// How the LAST inventory fetch went, per source. Only a merged read produces
// one: `GET /v1/parts` answers `{inventory, sources}`, where `sources` is
// `server/fanout.py`'s per-source ok/error status. A single-server read carries
// no such key and clears this back to `[]`.
//
// State, not an event, and for the sharpest possible reason: a merged view with
// a source down still answers 200 and still renders — it is simply missing that
// server's stock. Nothing in the rows themselves says so. This signal is the
// only thing that does, so it has to be readable by whatever renders *after*
// the fetch, not just by whoever was listening during it.

/**
 * @typedef {{id: string, name: string, ok: boolean, error: string,
 *            status: (number|null)}} SourceFetchStatus
 */

/** @type {{ get(): Array<SourceFetchStatus>, set(v: Array<SourceFetchStatus>): void, peek(): Array<SourceFetchStatus> }} */
export const sourceStatusSignal = signal(/** @type {Array<SourceFetchStatus>} */ ([]));
