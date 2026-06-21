// tests/js/route-scan-result.test.mjs
// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';

const overlay = vi.fn();
const grouping = vi.fn();
vi.mock('../../js/import/mfg-direct/ocr-overlay/ocr-overlay-panel.js', () => ({
  openOverlay: (...a) => overlay(...a),
}));
vi.mock('../../js/import/mfg-direct/scan-grouping.js', () => ({
  openGroupingEditor: (...a) => grouping(...a),
  buildGroupPayloads: () => [],
}));

// Stub heavy transitive deps that touch pywebview/fetch.
vi.mock('../../js/store.js', () => ({
  store: { vendors: [] },
  onInventoryUpdated: vi.fn(),
  loadVendorsAndPOs: vi.fn(),
}));
vi.mock('../../js/undo-redo.js', () => ({
  UndoRedo: { register: vi.fn(), save: vi.fn() },
}));
vi.mock('../../js/inventory/inv-state.js', () => ({
  recordImportGeneration: vi.fn(),
  popImportGeneration: vi.fn(),
}));
vi.mock('../../js/import/mfg-direct/mfg-direct-renderer.js', () => ({
  renderEditor: () => '',
  renderScanModal: () => '',
}));
vi.mock('../../js/vendor/qrcode.js', () => ({
  renderQrToCanvas: vi.fn(),
}));
vi.mock('../../js/import/mfg-direct/vendor-picker.js', () => ({
  createVendorPicker: () => ({ selectPseudoVendor: vi.fn(), onVendorNameBlur: vi.fn(), onVendorUrlBlur: vi.fn() }),
}));
vi.mock('../../js/import/mfg-direct/scan-shell.js', () => ({
  openScanShell: vi.fn(),
  markShellTile: vi.fn(),
  closeScanShell: vi.fn(),
}));

const { routeScanResult } = await import('../../js/import/mfg-direct/mfg-direct-panel.js');

describe('routeScanResult', () => {
  beforeEach(() => { overlay.mockClear(); grouping.mockClear(); });

  it('opens overlay for a single photo', () => {
    const photos = [{ index: 0, filename: 'a.png', image_b64: 'x',
      pages: [{ image_b64: 'x', width: 1, height: 1, words: [], lines: [] }],
      prefill_rows: [{ mpn: 'A' }] }];
    routeScanResult(photos, [[0]], 'generic');
    expect(overlay).toHaveBeenCalledTimes(1);
    expect(grouping).not.toHaveBeenCalled();
  });

  it('opens grouping editor for 2+ photos', () => {
    const photos = [
      { index: 0, filename: 'a.png', image_b64: 'x', pages: [], prefill_rows: [] },
      { index: 1, filename: 'b.png', image_b64: 'y', pages: [], prefill_rows: [] },
    ];
    routeScanResult(photos, [[0], [1]], 'lcsc');
    expect(grouping).toHaveBeenCalledTimes(1);
    expect(overlay).not.toHaveBeenCalled();
  });
});
