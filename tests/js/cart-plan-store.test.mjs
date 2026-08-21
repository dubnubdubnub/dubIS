import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('../../js/constants.js', () => ({
  SECTION_ORDER: [], FIELDNAMES: [], LABEL_EXPORT_CFG: {},
}));

const apiMock = vi.fn();
vi.mock('../../js/api.js', () => ({
  api: (...args) => apiMock(...args),
  AppLog: { warn: vi.fn(), error: vi.fn() },
}));

let activeCartId = 'cart_1';
vi.mock('../../js/cart/cart-store.js', () => ({
  getActiveCartId: () => activeCartId,
}));

describe('cart plan store', () => {
  let planStore;
  let store;

  beforeEach(async () => {
    vi.resetModules();
    vi.useFakeTimers();
    apiMock.mockReset();
    apiMock.mockImplementation(async () => ({ lines: [], totals: { spend: 0 } }));
    activeCartId = 'cart_1';
    planStore = await import('../../js/cart/cart-plan-store.js');
    store = await import('../../js/store.js');
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('starts with no plan and publishes that', () => {
    expect(planStore.getPlan()).toBeNull();
    expect(planStore.cartPlanSignal.get()).toEqual({ plan: null, loading: false, error: '' });
  });

  it('fetches the plan for the active cart', async () => {
    await planStore.loadPlan();
    expect(apiMock).toHaveBeenCalledWith('plan_cart', 'cart_1', 'min', 80);
    expect(planStore.getPlan()).toEqual({ lines: [], totals: { spend: 0 } });
  });

  it('publishes the loaded plan on the signal', async () => {
    await planStore.loadPlan();
    const state = planStore.cartPlanSignal.get();
    expect(state.plan.totals.spend).toBe(0);
    expect(state.loading).toBe(false);
  });

  it('sends the reel ceiling from preferences, not a hard-coded default', async () => {
    store.setBehaviorPrefs({ reelCeiling: 150 });
    await planStore.loadPlan();
    expect(apiMock).toHaveBeenCalledWith('plan_cart', 'cart_1', 'min', 150);
  });

  it('sends no ceiling rather than zero when the preference is unusable', async () => {
    // A ceiling of 0 would reject every reel there is, so it must not be sent
    // as one. store.js normalises it back to the default; reelCeiling() is the
    // second line of defence if that ever changes.
    store.setBehaviorPrefs({ reelCeiling: 0 });
    expect(planStore.reelCeiling()).toBe(80);
  });

  it('refetches when the cart-wide preset changes', async () => {
    await planStore.setPreset('reel');
    expect(planStore.getPreset()).toBe('reel');
    expect(apiMock).toHaveBeenCalledWith('plan_cart', 'cart_1', 'reel', 80);
  });

  it('clears the plan when there is no active cart', async () => {
    await planStore.loadPlan();
    expect(planStore.getPlan()).not.toBeNull();
    activeCartId = null;
    await planStore.loadPlan();
    expect(planStore.getPlan()).toBeNull();
    expect(apiMock).toHaveBeenCalledTimes(1);
  });

  it('keeps the previous plan on screen when a refresh fails, labelled stale', async () => {
    await planStore.loadPlan();
    // api() swallows errors and returns undefined after logging + toasting.
    apiMock.mockImplementationOnce(async () => undefined);
    await planStore.loadPlan();
    const state = planStore.cartPlanSignal.get();
    expect(state.plan).not.toBeNull();      // a stale total beats no total
    expect(state.error).toBeTruthy();
  });

  it('ignores a slow earlier response that lands after a newer one', async () => {
    // Dragging the board count fires several requests; completion order is not
    // guaranteed, and the stale winner would show a total for a board count
    // nobody is looking at any more.
    let releaseFirst;
    apiMock.mockImplementationOnce(() => new Promise((r) => { releaseFirst = () => r({ tag: 'first' }); }));
    apiMock.mockImplementationOnce(async () => ({ tag: 'second' }));

    const first = planStore.loadPlan();
    const second = planStore.loadPlan();
    await second;
    releaseFirst();
    await first;

    expect(planStore.getPlan()).toEqual({ tag: 'second' });
  });

  it('collapses a burst of scheduled refreshes into one request', async () => {
    planStore.schedulePlanRefresh();
    planStore.schedulePlanRefresh();
    planStore.schedulePlanRefresh();
    expect(apiMock).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(planStore.PLAN_DEBOUNCE_MS + 5);
    expect(apiMock).toHaveBeenCalledTimes(1);
  });

  it('does not fire a scheduled refresh before the debounce elapses', async () => {
    planStore.schedulePlanRefresh();
    await vi.advanceTimersByTimeAsync(planStore.PLAN_DEBOUNCE_MS - 10);
    expect(apiMock).not.toHaveBeenCalled();
  });

  it('an immediate load cancels a pending scheduled one', async () => {
    planStore.schedulePlanRefresh();
    await planStore.loadPlan();
    await vi.advanceTimersByTimeAsync(planStore.PLAN_DEBOUNCE_MS + 5);
    expect(apiMock).toHaveBeenCalledTimes(1);
  });

  it('clearPlan drops the plan and cancels pending work', async () => {
    await planStore.loadPlan();
    planStore.schedulePlanRefresh();
    planStore.clearPlan();
    expect(planStore.getPlan()).toBeNull();
    await vi.advanceTimersByTimeAsync(planStore.PLAN_DEBOUNCE_MS + 5);
    expect(apiMock).toHaveBeenCalledTimes(1);
  });

  it('a response arriving after clearPlan does not resurrect the plan', async () => {
    let release;
    apiMock.mockImplementationOnce(() => new Promise((r) => { release = () => r({ tag: 'late' }); }));
    const inflight = planStore.loadPlan();
    planStore.clearPlan();
    release();
    await inflight;
    expect(planStore.getPlan()).toBeNull();
  });
});
