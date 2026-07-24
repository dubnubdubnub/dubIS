import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock the api module the store calls.
vi.mock('../../js/api.js', () => ({
  api: vi.fn(),
  AppLog: { warn() {}, error() {} },
}));
// cart-store.js imports store.js (for prefillName()'s store.bomFileName
// read) — store.js imports js/constants.js, which has a top-level `await
// fetch` that crashes vitest collection (see CLAUDE.md's "Don't import
// js/constants.js in test setup" trap); mock it the same way tests/js/store.test.js does.
vi.mock('../../js/constants.js', () => ({ SECTION_ORDER: [], FIELDNAMES: [] }));
vi.mock('../../js/ui-helpers.js', () => ({ formatMoney: vi.fn() }));
import { api } from '../../js/api.js';
import * as cartStore from '../../js/cart/cart-store.js';
import { cartsSignal } from '../../js/signals.js';
import { store } from '../../js/store.js';

describe('cart-store', () => {
  beforeEach(() => { api.mockReset(); store.bomFileName = ''; });

  it('loadCarts stores carts and active id', async () => {
    api.mockResolvedValueOnce({ carts: [{ id: 'cart_1', name: 'A', items: [{ ref: 'x', qty: 2 }] }], active_cart_id: 'cart_1' });
    await cartStore.loadCarts();
    expect(cartStore.getActiveCartId()).toBe('cart_1');
    expect(cartStore.cartItemCount()).toBe(1);
  });

  it('getCarts and getActiveCart reflect loaded state', async () => {
    api.mockResolvedValueOnce({
      carts: [
        { id: 'cart_1', name: 'A', items: [] },
        { id: 'cart_2', name: 'B', items: [{ ref: 'y', qty: 1 }] },
      ],
      active_cart_id: 'cart_2',
    });
    await cartStore.loadCarts();
    expect(cartStore.getCarts()).toHaveLength(2);
    expect(cartStore.getActiveCart().id).toBe('cart_2');
    expect(cartStore.cartItemCount()).toBe(1);
  });

  it('loadCarts publishes cartsSignal', async () => {
    api.mockResolvedValueOnce({ carts: [{ id: 'cart_3', name: 'C', items: [] }], active_cart_id: 'cart_3' });
    await cartStore.loadCarts();
    expect(cartsSignal.get()).toEqual({ carts: [{ id: 'cart_3', name: 'C', items: [] }], activeCartId: 'cart_3' });
  });

  it('addToActiveCart posts item to the active cart then reloads', async () => {
    api.mockResolvedValueOnce({ carts: [{ id: 'cart_1', name: 'A', items: [] }], active_cart_id: 'cart_1' });
    await cartStore.loadCarts();

    api.mockResolvedValueOnce({}); // add_cart_item response (unwrapped detail)
    api.mockResolvedValueOnce({ carts: [{ id: 'cart_1', name: 'A', items: [{ ref: 'z', qty: 5 }] }], active_cart_id: 'cart_1' });

    await cartStore.addToActiveCart({ partId: 'part_1', raw: null, qty: 5, shortfall: 0, targetDistributor: 'lcsc' });

    expect(api).toHaveBeenCalledWith('add_cart_item', 'cart_1', 'part_1', null, 5, 'lcsc', 0);
    expect(cartStore.cartItemCount()).toBe(1);
  });

  it('createCart calls the api and reloads carts', async () => {
    api.mockResolvedValueOnce({ id: 'cart_9', name: 'New', items: [] }); // create_cart (unwrapped)
    api.mockResolvedValueOnce({ carts: [{ id: 'cart_9', name: 'New', items: [] }], active_cart_id: 'cart_9' });

    await cartStore.createCart('New');

    expect(api).toHaveBeenCalledWith('create_cart', 'New');
    expect(cartStore.getActiveCartId()).toBe('cart_9');
  });

  it('addToActiveCart auto-creates and activates a cart on first use (no active cart yet)', async () => {
    // Fresh install: list_carts() -> no carts, no active id.
    api.mockResolvedValueOnce({ carts: [], active_cart_id: null });
    await cartStore.loadCarts();
    expect(cartStore.getActiveCartId()).toBeNull();

    // create_cart(prefillName()) -> new cart (create_cart does NOT itself
    // activate — cart-store must call set_active_cart explicitly).
    api.mockResolvedValueOnce({ id: 'cart_new', name: expect.any(String), items: [] });
    // createCart()'s internal loadCarts() refetch.
    api.mockResolvedValueOnce({ carts: [{ id: 'cart_new', name: 'N', items: [] }], active_cart_id: null });
    // set_active_cart(cart_new) call.
    api.mockResolvedValueOnce({ active_cart_id: 'cart_new' });
    // setActiveCart()'s internal loadCarts() refetch.
    api.mockResolvedValueOnce({ carts: [{ id: 'cart_new', name: 'N', items: [] }], active_cart_id: 'cart_new' });
    // add_cart_item call.
    api.mockResolvedValueOnce({});
    // addToActiveCart()'s final loadCarts() refetch.
    api.mockResolvedValueOnce({ carts: [{ id: 'cart_new', name: 'N', items: [{ ref: 'p', qty: 1 }] }], active_cart_id: 'cart_new' });

    await cartStore.addToActiveCart({ partId: 'p', qty: 1 });

    expect(api).toHaveBeenCalledWith('create_cart', expect.any(String));
    expect(api).toHaveBeenCalledWith('set_active_cart', 'cart_new');
    expect(api).toHaveBeenCalledWith('add_cart_item', 'cart_new', 'p', null, 1, null, null);
    expect(cartStore.getActiveCartId()).toBe('cart_new');
    expect(cartStore.cartItemCount()).toBe(1);
  });

  it('addToActiveCart memoizes concurrent first-adds into ONE create_cart call (no duplicate orphaned cart)', async () => {
    // Fresh install: list_carts() -> no carts, no active id. Stateful mock
    // (rather than a mockResolvedValueOnce queue) because the two concurrent
    // addToActiveCart() calls interleave their api() calls unpredictably —
    // only the memoization under test keeps this consistent.
    let activeCartId = null;
    const carts = [];
    api.mockImplementation(async (cmd, ...args) => {
      switch (cmd) {
        case 'list_carts':
          return { carts, active_cart_id: activeCartId };
        case 'create_cart': {
          const created = { id: 'cart_new', name: args[0], items: [] };
          carts.push(created);
          return created;
        }
        case 'set_active_cart':
          activeCartId = args[0];
          return { active_cart_id: activeCartId };
        case 'add_cart_item':
          return {};
        default:
          return {};
      }
    });

    await cartStore.loadCarts();
    expect(cartStore.getActiveCartId()).toBeNull();

    await Promise.all([
      cartStore.addToActiveCart({ partId: 'p1', qty: 1 }),
      cartStore.addToActiveCart({ partId: 'p2', qty: 2 }),
    ]);

    const createCartCalls = api.mock.calls.filter(([cmd]) => cmd === 'create_cart');
    expect(createCartCalls).toHaveLength(1);
    expect(cartStore.getActiveCartId()).toBe('cart_new');
    expect(carts).toHaveLength(1);
  });

  it('addBomMissing auto-creates a cart on first use when no cartId is passed', async () => {
    api.mockResolvedValueOnce({ carts: [], active_cart_id: null });
    await cartStore.loadCarts();

    api.mockResolvedValueOnce({ id: 'cart_bom', name: 'B', items: [] });
    api.mockResolvedValueOnce({ carts: [{ id: 'cart_bom', name: 'B', items: [] }], active_cart_id: null });
    api.mockResolvedValueOnce({ active_cart_id: 'cart_bom' });
    api.mockResolvedValueOnce({ carts: [{ id: 'cart_bom', name: 'B', items: [] }], active_cart_id: 'cart_bom' });
    api.mockResolvedValueOnce({ id: 'cart_bom', name: 'B', items: [{ ref: 'x', qty: 3 }] });
    api.mockResolvedValueOnce({ carts: [{ id: 'cart_bom', name: 'B', items: [{ ref: 'x', qty: 3 }] }], active_cart_id: 'cart_bom' });

    const missing = [{ part_id: 'x', qty: 3 }];
    await cartStore.addBomMissing(missing);

    expect(api).toHaveBeenCalledWith('create_cart', expect.any(String));
    expect(api).toHaveBeenCalledWith('add_bom_missing_to_cart', 'cart_bom', missing);
  });

  it('prefillName combines today\'s date with the loaded BOM filename', () => {
    store.bomFileName = 'widgets.csv';
    const today = new Date().toISOString().slice(0, 10);
    expect(cartStore.prefillName()).toBe(`${today} · widgets.csv`);
  });

  it('prefillName is just the date when no BOM is loaded', () => {
    store.bomFileName = '';
    const today = new Date().toISOString().slice(0, 10);
    expect(cartStore.prefillName()).toBe(today);
  });
});
