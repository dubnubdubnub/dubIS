// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

var _mockVendors = [];
var _mockInventory = [];

// `onInventoryUpdated` is a STANDALONE named export of store.js — NOT a method on
// the `store` object. The mock mirrors that reality so the test would catch the
// regression where the code called `store.onInventoryUpdated(...)` (undefined → throws).
var _mockOnInventoryUpdated = vi.fn();

vi.mock('../../js/store.js', () => ({
  store: {
    get vendors() { return _mockVendors; },
    get inventory() { return _mockInventory; },
  },
  onInventoryUpdated: (...args) => _mockOnInventoryUpdated(...args),
}));

var _apiCalls = [];
var _apiVendorsCalls = { upsert: [], merge: [], fetchFavicon: [] };
// Per-test override of resolved/rejected behavior for the api/apiVendors mocks.
var _rebuildResult = [];
var _upsertImpl = null;
var _mockAppLogError = vi.fn();
var _mockAppLogWarn = vi.fn();
var _mockShowToast = vi.fn();

vi.mock('../../js/api.js', () => ({
  api: vi.fn((...args) => {
    _apiCalls.push(args);
    return Promise.resolve(_rebuildResult);
  }),
  apiVendors: {
    upsert: vi.fn((...args) => {
      _apiVendorsCalls.upsert.push(args);
      if (_upsertImpl) return _upsertImpl(...args);
      return Promise.resolve({});
    }),
    merge: vi.fn((...args) => {
      _apiVendorsCalls.merge.push(args);
      return Promise.resolve({});
    }),
    fetchFavicon: vi.fn((...args) => {
      _apiVendorsCalls.fetchFavicon.push(args);
      return Promise.resolve({});
    }),
  },
  AppLog: {
    error: (...args) => _mockAppLogError(...args),
    warn: (...args) => _mockAppLogWarn(...args),
  },
}));

vi.mock('../../js/ui-helpers.js', () => ({
  escHtml: vi.fn(s => String(s || '')),
  showToast: (...args) => _mockShowToast(...args),
}));

import { openVendorPopover, closeVendorPopover } from '../../js/inventory/vendor-flyout.js';

beforeEach(() => {
  _mockVendors = [];
  _mockInventory = [];
  _rebuildResult = [];
  _upsertImpl = null;
  _mockOnInventoryUpdated.mockClear();
  _mockAppLogError.mockClear();
  _mockAppLogWarn.mockClear();
  _mockShowToast.mockClear();
  _apiCalls.length = 0;
  _apiVendorsCalls.upsert.length = 0;
  _apiVendorsCalls.merge.length = 0;
  _apiVendorsCalls.fetchFavicon.length = 0;
  document.body.innerHTML = '';
});

afterEach(() => {
  closeVendorPopover();
  document.body.innerHTML = '';
});

describe('openVendorPopover', () => {
  it('appends a .vendor-popover element to the body', () => {
    var anchor = document.createElement('span');
    document.body.appendChild(anchor);
    _mockVendors = [{ id: 'v1', name: 'LCSC', url: 'https://lcsc.com', icon: '', favicon_path: '' }];

    openVendorPopover(anchor, 'v1');

    var popover = document.querySelector('.vendor-popover');
    expect(popover).not.toBeNull();
    expect(popover.dataset.vendorId).toBe('v1');
  });

  it('shows vendor name in the popover header', () => {
    var anchor = document.createElement('span');
    document.body.appendChild(anchor);
    _mockVendors = [{ id: 'v1', name: 'My Vendor', url: '', icon: '', favicon_path: '' }];

    openVendorPopover(anchor, 'v1');

    var title = document.querySelector('.vendor-popover-title');
    expect(title.textContent).toContain('My Vendor');
  });

  it('shows URL field for real vendors', () => {
    var anchor = document.createElement('span');
    document.body.appendChild(anchor);
    _mockVendors = [{ id: 'v_real', name: 'Real', url: 'https://real.com', icon: '', favicon_path: '' }];

    openVendorPopover(anchor, 'v_real');

    var urlInput = document.querySelector('[data-field="url"]');
    expect(urlInput).not.toBeNull();
    expect(urlInput.value).toBe('https://real.com');
  });

  it('hides URL field for pseudo vendors (v_self, v_salvage, v_unknown)', () => {
    var anchor = document.createElement('span');
    document.body.appendChild(anchor);
    _mockVendors = [{ id: 'v_self', name: 'Self', url: '', icon: '⚙️', favicon_path: '' }];

    openVendorPopover(anchor, 'v_self');

    var urlInput = document.querySelector('[data-field="url"]');
    expect(urlInput).toBeNull();
  });

  it('shows refresh favicon button for real vendors', () => {
    var anchor = document.createElement('span');
    document.body.appendChild(anchor);
    _mockVendors = [{ id: 'v1', name: 'Real', url: 'https://real.com', icon: '', favicon_path: '' }];

    openVendorPopover(anchor, 'v1');

    var btn = document.querySelector('.vendor-popover-refresh');
    expect(btn).not.toBeNull();
  });

  it('hides refresh favicon button for pseudo vendors', () => {
    var anchor = document.createElement('span');
    document.body.appendChild(anchor);
    _mockVendors = [{ id: 'v_salvage', name: 'Salvage', url: '', icon: '♻️', favicon_path: '' }];

    openVendorPopover(anchor, 'v_salvage');

    var btn = document.querySelector('.vendor-popover-refresh');
    expect(btn).toBeNull();
  });

  it('closes previous popover when opening a new one', () => {
    var anchor = document.createElement('span');
    document.body.appendChild(anchor);
    _mockVendors = [
      { id: 'v1', name: 'V1', url: '', icon: '', favicon_path: '' },
      { id: 'v2', name: 'V2', url: '', icon: '', favicon_path: '' },
    ];

    openVendorPopover(anchor, 'v1');
    openVendorPopover(anchor, 'v2');

    var popovers = document.querySelectorAll('.vendor-popover');
    expect(popovers.length).toBe(1);
    expect(popovers[0].dataset.vendorId).toBe('v2');
  });

  it('falls back gracefully when vendor is not in store', () => {
    var anchor = document.createElement('span');
    document.body.appendChild(anchor);
    _mockVendors = [];

    openVendorPopover(anchor, 'v_missing');

    var popover = document.querySelector('.vendor-popover');
    expect(popover).not.toBeNull();
  });
});

describe('closeVendorPopover', () => {
  it('removes the popover from DOM', () => {
    var anchor = document.createElement('span');
    document.body.appendChild(anchor);
    _mockVendors = [{ id: 'v1', name: 'V1', url: '', icon: '', favicon_path: '' }];

    openVendorPopover(anchor, 'v1');
    expect(document.querySelector('.vendor-popover')).not.toBeNull();

    closeVendorPopover();
    expect(document.querySelector('.vendor-popover')).toBeNull();
  });

  it('is a no-op when no popover is open', () => {
    expect(() => closeVendorPopover()).not.toThrow();
  });
});

describe('save button', () => {
  it('calls apiVendors.upsert with vendorId, name, and url', async () => {
    var anchor = document.createElement('span');
    document.body.appendChild(anchor);
    _mockVendors = [{ id: 'v1', name: 'Old Name', url: 'https://old.com', icon: '', favicon_path: '' }];

    openVendorPopover(anchor, 'v1');

    var nameInput = /** @type {HTMLInputElement} */ (document.querySelector('[data-field="name"]'));
    var urlInput = /** @type {HTMLInputElement} */ (document.querySelector('[data-field="url"]'));
    nameInput.value = 'New Name';
    urlInput.value = 'https://new.com';

    document.querySelector('.vendor-popover-save').click();
    await new Promise(r => setTimeout(r, 0));

    expect(_apiVendorsCalls.upsert.length).toBe(1);
    expect(_apiVendorsCalls.upsert[0]).toEqual(['v1', 'New Name', 'https://new.com']);
  });

  it('refreshes the grid (onInventoryUpdated) and closes the popover on success', async () => {
    var anchor = document.createElement('span');
    document.body.appendChild(anchor);
    _mockVendors = [{ id: 'v1', name: 'Old Name', url: 'https://old.com', icon: '', favicon_path: '' }];
    var fresh = [{ id: 'p1', name: 'Part 1' }];
    _rebuildResult = fresh;

    openVendorPopover(anchor, 'v1');
    document.querySelector('.vendor-popover-save').click();

    await vi.waitFor(() => {
      expect(_mockOnInventoryUpdated).toHaveBeenCalledTimes(1);
    });
    expect(_mockOnInventoryUpdated).toHaveBeenCalledWith(fresh);
    expect(document.querySelector('.vendor-popover')).toBeNull();
    expect(_mockAppLogError).not.toHaveBeenCalled();
  });

  it('surfaces the failure (AppLog.error + showToast) and keeps the popover open on error', async () => {
    var anchor = document.createElement('span');
    document.body.appendChild(anchor);
    _mockVendors = [{ id: 'v1', name: 'Old Name', url: 'https://old.com', icon: '', favicon_path: '' }];
    _upsertImpl = () => Promise.reject(new Error('boom'));

    openVendorPopover(anchor, 'v1');
    document.querySelector('.vendor-popover-save').click();

    await vi.waitFor(() => {
      expect(_mockAppLogError).toHaveBeenCalledTimes(1);
    });
    expect(_mockShowToast).toHaveBeenCalledWith('Failed to save vendor');
    expect(_mockOnInventoryUpdated).not.toHaveBeenCalled();
    // Popover stays OPEN so the user can retry.
    expect(document.querySelector('.vendor-popover')).not.toBeNull();
  });
});

describe('merge select', () => {
  it('refreshes the grid (onInventoryUpdated) and closes the popover on success', async () => {
    var anchor = document.createElement('span');
    document.body.appendChild(anchor);
    _mockVendors = [
      { id: 'v1', name: 'Source', url: 'https://src.com', icon: '', favicon_path: '' },
      { id: 'v2', name: 'Dest', url: 'https://dst.com', icon: '', favicon_path: '' },
    ];
    var fresh = [{ id: 'p1', name: 'Part 1' }];
    _rebuildResult = fresh;
    var confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    openVendorPopover(anchor, 'v1');
    var select = /** @type {HTMLSelectElement} */ (document.querySelector('.vendor-popover-merge-select'));
    select.value = 'v2';
    select.dispatchEvent(new Event('change'));

    await vi.waitFor(() => {
      expect(_mockOnInventoryUpdated).toHaveBeenCalledTimes(1);
    });
    expect(_apiVendorsCalls.merge.length).toBe(1);
    expect(_apiVendorsCalls.merge[0]).toEqual(['v1', 'v2']);
    expect(_mockOnInventoryUpdated).toHaveBeenCalledWith(fresh);
    expect(document.querySelector('.vendor-popover')).toBeNull();
    confirmSpy.mockRestore();
  });
});
