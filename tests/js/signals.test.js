import { describe, it, expect, vi } from 'vitest';
import { signal, computed, effect, batch } from '../../js/signals.js';

// ── signal basic get/set ──────────────────────────────────

describe('signal', () => {
  it('returns initial value', () => {
    const s = signal(42);
    expect(s.get()).toBe(42);
  });

  it('updates value via set', () => {
    const s = signal(0);
    s.set(7);
    expect(s.get()).toBe(7);
  });

  it('peek() returns current value without subscribing', () => {
    const s = signal(10);
    s.set(20);
    expect(s.peek()).toBe(20);
  });

  it('does not notify when set to same value', () => {
    const s = signal(5);
    const spy = vi.fn();
    effect(() => { s.get(); spy(); });
    spy.mockClear();
    s.set(5); // same value
    expect(spy).not.toHaveBeenCalled();
  });
});

// ── effect dependency tracking ────────────────────────────

describe('effect', () => {
  it('runs immediately on creation', () => {
    const spy = vi.fn();
    effect(spy);
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it('re-runs when a read signal changes', () => {
    const s = signal(1);
    const spy = vi.fn();
    effect(() => { s.get(); spy(); });
    expect(spy).toHaveBeenCalledTimes(1);
    s.set(2);
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it('does not re-run on unread signal changes', () => {
    const a = signal(1);
    const b = signal(100);
    const spy = vi.fn();
    effect(() => { a.get(); spy(); });
    spy.mockClear();
    b.set(200); // b is not read by the effect
    expect(spy).not.toHaveBeenCalled();
  });

  it('cleanup: disposed effect does not run', () => {
    const s = signal(0);
    const spy = vi.fn();
    const dispose = effect(() => { s.get(); spy(); });
    spy.mockClear();
    dispose();
    s.set(1);
    expect(spy).not.toHaveBeenCalled();
  });

  it('cleanup: dispose unsubscribes from all signals', () => {
    const a = signal(0);
    const b = signal(0);
    const spy = vi.fn();
    const dispose = effect(() => { a.get(); b.get(); spy(); });
    spy.mockClear();
    dispose();
    a.set(1);
    b.set(1);
    expect(spy).not.toHaveBeenCalled();
  });

  it('tracks changing dependency set across re-runs', () => {
    const toggle = signal(true);
    const a = signal('A');
    const b = signal('B');
    const log = [];
    effect(() => { log.push(toggle.get() ? a.get() : b.get()); });
    expect(log).toEqual(['A']);
    a.set('A2');          // still tracking a
    expect(log).toEqual(['A', 'A2']);
    toggle.set(false);    // now tracks b instead of a
    expect(log).toEqual(['A', 'A2', 'B']);
    a.set('A3');          // a is no longer tracked — no re-run
    expect(log).toEqual(['A', 'A2', 'B']);
    b.set('B2');
    expect(log).toEqual(['A', 'A2', 'B', 'B2']);
  });

  it('peek() inside effect does not create a dependency', () => {
    const s = signal(0);
    const spy = vi.fn();
    effect(() => { s.peek(); spy(); });
    spy.mockClear();
    s.set(1);
    expect(spy).not.toHaveBeenCalled();
  });
});

// ── computed lazy evaluation ──────────────────────────────

describe('computed', () => {
  it('returns derived value', () => {
    const s = signal(3);
    const c = computed(() => s.get() * 2);
    expect(c.get()).toBe(6);
  });

  it('recomputes when dependency changes', () => {
    const s = signal(5);
    const c = computed(() => s.get() + 1);
    expect(c.get()).toBe(6);
    s.set(10);
    expect(c.get()).toBe(11);
  });

  it('peek() returns last computed value', () => {
    const s = signal(2);
    const c = computed(() => s.get() * 3);
    c.get(); // trigger initial compute
    expect(c.peek()).toBe(6);
  });

  it('computed is readable inside an effect', () => {
    const s = signal(1);
    const doubled = computed(() => s.get() * 2);
    const results = [];
    effect(() => { results.push(doubled.get()); });
    s.set(5);
    expect(results).toEqual([2, 10]);
  });

  it('does not recompute unnecessarily (same value)', () => {
    const s = signal(0);
    const fn = vi.fn(() => Math.abs(s.get()));
    const c = computed(fn);
    c.get();
    fn.mockClear();
    s.set(0); // same value — signal skips notify
    c.get();
    expect(fn).not.toHaveBeenCalled();
  });
});

// ── batch ─────────────────────────────────────────────────

describe('batch', () => {
  it('coalesces multiple sets into one effect run', () => {
    const a = signal(0);
    const b = signal(0);
    const spy = vi.fn();
    effect(() => { a.get(); b.get(); spy(); });
    spy.mockClear();
    batch(() => {
      a.set(1);
      b.set(2);
    });
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it('nested batch: outer flushes once', () => {
    const s = signal(0);
    const spy = vi.fn();
    effect(() => { s.get(); spy(); });
    spy.mockClear();
    batch(() => {
      s.set(1);
      batch(() => { s.set(2); });
    });
    // Should only flush once (from the outer batch)
    expect(spy).toHaveBeenCalledTimes(1);
    expect(s.get()).toBe(2);
  });

  it('effect sees final value after batch', () => {
    const s = signal(0);
    const seen = [];
    effect(() => { seen.push(s.get()); });
    batch(() => {
      s.set(1);
      s.set(2);
      s.set(3);
    });
    // Initial run + one batch notification
    expect(seen).toEqual([0, 3]);
  });
});

// ── re-entry guard ────────────────────────────────────────

describe('re-entry', () => {
  it('effect that calls set on its own dep does not loop infinitely', () => {
    const s = signal(0);
    const spy = vi.fn();
    // Effect reads s and tries to set it — should not recurse
    const dispose = effect(() => {
      const v = s.get();
      spy(v);
      if (v < 2) s.set(v + 1); // this set happens outside the "running" guard
    });
    // Should stabilise: 0 → set(1) → 1 → set(2) → 2 (stop)
    expect(s.peek()).toBe(2);
    expect(spy).toHaveBeenCalledTimes(3);
    dispose();
  });
});
