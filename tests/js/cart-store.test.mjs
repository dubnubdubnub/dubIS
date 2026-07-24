import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock the api module the store calls.
vi.mock('../../js/api.js', () => ({
  api: vi.fn(),
  AppLog: { warn() {}, error() {} },
}));
import { api } from '../../js/api.js';
import * as cartStore from '../../js/cart/cart-store.js';
import { cartsSignal } from '../../js/signals.js';

describe('cart-store', () => {
  beforeEach(() => { api.mockReset(); });

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

  it('throws when addToActiveCart is called with no active cart', async () => {
    api.mockResolvedValueOnce({ carts: [], active_cart_id: null });
    await cartStore.loadCarts();
    await expect(cartStore.addToActiveCart({ partId: 'p', qty: 1 })).rejects.toThrow();
  });
});
