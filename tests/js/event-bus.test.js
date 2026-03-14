import { describe, it, expect, vi, beforeEach } from 'vitest';
import { EventBus, Events } from '../../js/event-bus.js';

describe('Events', () => {
  it('is frozen (immutable)', () => {
    expect(Object.isFrozen(Events)).toBe(true);
  });

  it('contains all expected event names', () => {
    expect(Events.INVENTORY_LOADED).toBe('inventory-loaded');
    expect(Events.INVENTORY_UPDATED).toBe('inventory-updated');
    expect(Events.BOM_LOADED).toBe('bom-loaded');
    expect(Events.BOM_CLEARED).toBe('bom-cleared');
    expect(Events.PREFS_CHANGED).toBe('preferences-changed');
    expect(Events.CONFIRMED_CHANGED).toBe('confirmed-match-changed');
    expect(Events.LINKING_MODE).toBe('linking-mode');
    expect(Events.LINKS_CHANGED).toBe('links-changed');
    expect(Events.SAVE_AND_CLOSE).toBe('save-and-close');
  });
});

describe('EventBus', () => {
  beforeEach(() => {
    EventBus._listeners = {};
  });

  it('calls listener when event is emitted', () => {
    const fn = vi.fn();
    EventBus.on('test', fn);
    EventBus.emit('test', { x: 1 });
    expect(fn).toHaveBeenCalledWith({ x: 1 });
  });

  it('supports multiple listeners on the same event', () => {
    const fn1 = vi.fn();
    const fn2 = vi.fn();
    EventBus.on('test', fn1);
    EventBus.on('test', fn2);
    EventBus.emit('test', 'data');
    expect(fn1).toHaveBeenCalledWith('data');
    expect(fn2).toHaveBeenCalledWith('data');
  });

  it('does not call listener after off()', () => {
    const fn = vi.fn();
    EventBus.on('test', fn);
    EventBus.off('test', fn);
    EventBus.emit('test');
    expect(fn).not.toHaveBeenCalled();
  });

  it('only removes the specific listener with off()', () => {
    const fn1 = vi.fn();
    const fn2 = vi.fn();
    EventBus.on('test', fn1);
    EventBus.on('test', fn2);
    EventBus.off('test', fn1);
    EventBus.emit('test');
    expect(fn1).not.toHaveBeenCalled();
    expect(fn2).toHaveBeenCalled();
  });

  it('does not throw when emitting event with no listeners', () => {
    expect(() => EventBus.emit('nonexistent')).not.toThrow();
  });

  it('does not throw when removing listener from event with no listeners', () => {
    expect(() => EventBus.off('nonexistent', () => {})).not.toThrow();
  });

  it('passes undefined data when emitting without payload', () => {
    const fn = vi.fn();
    EventBus.on('test', fn);
    EventBus.emit('test');
    expect(fn).toHaveBeenCalledWith(undefined);
  });
});
