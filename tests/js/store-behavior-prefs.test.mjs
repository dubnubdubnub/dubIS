import { describe, it, expect, vi, beforeEach } from 'vitest';

// constants.js has top-level fetch that crashes vitest; mock it first.
vi.mock('../../js/constants.js', () => ({
  SECTION_ORDER: [],
  FIELDNAMES: [],
  LABEL_EXPORT_CFG: {},
}));

// api.js has network side effects; stub it before importing the store.
vi.mock('../../js/api.js', () => ({
  api: vi.fn(async () => ({})),
  AppLog: { warn: vi.fn(), error: vi.fn() },
}));

describe('behavior preferences slice', () => {
  let store;
  beforeEach(async () => {
    vi.resetModules();
    store = await import('../../js/store.js');
  });

  it('defaults autoCopySelection to false', () => {
    expect(store.getBehaviorPrefs()).toEqual({ autoCopySelection: false });
  });

  it('setBehaviorPrefs updates the value and persists', async () => {
    const { api } = await import('../../js/api.js');
    store.setBehaviorPrefs({ autoCopySelection: true });
    expect(store.getBehaviorPrefs().autoCopySelection).toBe(true);
    expect(api).toHaveBeenCalledWith('save_preferences', expect.any(String));
  });

  it('coerces non-boolean input to boolean', () => {
    store.setBehaviorPrefs({ autoCopySelection: 'yes' });
    expect(store.getBehaviorPrefs().autoCopySelection).toBe(true);
  });
});
