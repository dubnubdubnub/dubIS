import { test, expect } from 'vitest';
import { runFetchMissingDescriptions } from '../../js/inventory/fetch-descriptions-command.js';

test('fetches, updates store, and toasts the summary', async () => {
  const calls = { toasts: [], updated: null };
  const deps = {
    api: async () => ({ inventory: [{ mpn: 'X' }], summary: { updated: 3, failed: 1, skipped: 2 } }),
    onInventoryUpdated: (inv) => { calls.updated = inv; },
    showToast: (m) => calls.toasts.push(m),
  };
  await runFetchMissingDescriptions(deps);
  expect(calls.updated).toEqual([{ mpn: 'X' }]);
  expect(calls.toasts[0]).toMatch(/3/);
  expect(calls.toasts[0]).toMatch(/fail/i);
});

test('no-op toast when nothing was updated', async () => {
  const calls = { toasts: [] };
  const deps = {
    api: async () => ({ inventory: [], summary: { updated: 0, failed: 0, skipped: 5 } }),
    onInventoryUpdated: () => {},
    showToast: (m) => calls.toasts.push(m),
  };
  await runFetchMissingDescriptions(deps);
  expect(calls.toasts[0]).toMatch(/no .*descriptions|nothing/i);
});

test('bails without crashing when api returns undefined', async () => {
  const deps = { api: async () => undefined, onInventoryUpdated: () => { throw new Error('should not be called'); }, showToast: () => {} };
  await runFetchMissingDescriptions(deps);  // must not throw
});
