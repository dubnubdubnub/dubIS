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
