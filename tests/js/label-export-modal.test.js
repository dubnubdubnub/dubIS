// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Mocks ───────────────────────────────────────────────────────────────────
const showToast = vi.fn();
vi.mock('../../js/ui-helpers.js', () => ({
  showToast: (...a) => showToast(...a),
  escHtml: (s) => s || '',
  // Real-enough Modal: toggles the `.hidden` class on the element so tests
  // can assert open/close. cancelId wiring mirrors the real helper.
  Modal: (id, { cancelId } = {}) => {
    const el = document.getElementById(id);
    const open = () => el && el.classList.remove('hidden');
    const close = () => el && el.classList.add('hidden');
    if (cancelId) {
      const c = document.getElementById(cancelId);
      if (c) c.addEventListener('click', close);
    }
    return { el, open, close };
  },
}));

const api = vi.fn().mockResolvedValue({ path: 'C:/out/file.csv' });
const AppLog = { info: vi.fn(), warn: vi.fn(), error: vi.fn(), clear: vi.fn() };
vi.mock('../../js/api.js', () => ({
  api: (...a) => api(...a),
  AppLog: { info: (...a) => AppLog.info(...a), warn: (...a) => AppLog.warn(...a), error: (...a) => AppLog.error(...a), clear: (...a) => AppLog.clear(...a) },
}));

let registeredHandler = null;
vi.mock('../../js/label-selection.js', () => ({
  setPreviewHandler: (fn) => { registeredHandler = fn; },
}));

// Real cfg from constants.json (mirrored here to avoid the top-level fetch in
// constants.js, which jsdom can't satisfy). Inlined in the factory because
// vi.mock is hoisted above module-level consts.
vi.mock('../../js/constants.js', () => ({
  LABEL_EXPORT_CFG: {
    tape6:  { budget_mm: 115, font_pt: 10 },
    tape12: { budget_mm: 40, preferred_mm: 30, font_pt: 10, rows: 3 },
    calibration_k: 0.085,
    char_width: { narrow: 0.32, wide: 0.95, default: 0.6 },
    narrow_chars: "iIl.,:;'`|!()[]{} ",
    wide_chars: 'mMW@%#',
    distributor_prefix: { v_lcsc: '', v_digikey: '', v_mouser: '', v_pololu: '' },
    header_row: false,
  },
}));

import { init, openWith, doExport } from '../../js/label-export-modal.js';
import { estimateWidthMm } from '../../js/label-export.js';
import { LABEL_EXPORT_CFG as CFG } from '../../js/constants.js';

// ── DOM scaffold matching index.html ────────────────────────────────────────
function buildDom() {
  document.body.innerHTML = `
    <div class="modal-overlay hidden" id="label-export-modal">
      <div class="modal modal-wide">
        <div class="label-tape-toggle" id="label-export-tape-toggle">
          <label><input type="radio" name="label-export-tape" value="6mm" checked></label>
          <label><input type="radio" name="label-export-tape" value="12mm"></label>
        </div>
        <div class="label-export-preview" id="label-export-preview"></div>
        <div class="label-export-help" id="label-export-help"></div>
        <button id="label-export-cancel">Cancel</button>
        <button id="label-export-do">Export</button>
      </div>
    </div>
    <div class="toast" id="toast"></div>`;
}

function rows() {
  return Array.from(document.querySelectorAll('.label-export-row'));
}
function editCells(row) {
  return Array.from(row.querySelectorAll('.label-edit-cell'));
}

beforeEach(() => {
  buildDom();
  registeredHandler = null;
  showToast.mockClear();
  api.mockClear();
  api.mockResolvedValue({ path: 'C:/out/file.csv' });
  AppLog.info.mockClear();
  AppLog.warn.mockClear();
  AppLog.error.mockClear();
  init();
});

const sampleItems = () => [
  { mpn: 'RC0402', lcsc: 'C1', description: '10k resistor', primary_vendor_id: 'v_lcsc' },
  { mpn: 'GRM155', lcsc: 'C2', description: '100nf cap',    primary_vendor_id: 'v_lcsc' },
  { mpn: 'CP2102', digikey: 'D1', description: 'usb bridge', primary_vendor_id: 'v_digikey' },
];

describe('init', () => {
  it('registers the preview handler', () => {
    expect(typeof registeredHandler).toBe('function');
  });
  it('fills the footer help text', () => {
    expect(document.getElementById('label-export-help').innerHTML).toContain('Epson');
  });
});

describe('openWith — 6mm', () => {
  it('renders one row per item with a single editable cell, grouped by distributor', () => {
    openWith(sampleItems(), '6mm');
    expect(document.getElementById('label-export-modal').classList.contains('hidden')).toBe(false);
    expect(rows()).toHaveLength(3);
    rows().forEach((r) => expect(editCells(r)).toHaveLength(1));
    // Two groups: v_lcsc (2) and v_digikey (1)
    const groups = document.querySelectorAll('.label-export-group');
    expect(groups).toHaveLength(2);
    expect(groups[0].dataset.vendor).toBe('v_lcsc');
    expect(groups[1].dataset.vendor).toBe('v_digikey');
  });

  it('does not open with an empty selection', () => {
    openWith([], '6mm');
    expect(document.getElementById('label-export-modal').classList.contains('hidden')).toBe(true);
    expect(showToast).toHaveBeenCalled();
  });
});

describe('openWith — 12mm', () => {
  it('renders three editable cells per row', () => {
    openWith(sampleItems(), '12mm');
    expect(rows()).toHaveLength(3);
    rows().forEach((r) => expect(editCells(r)).toHaveLength(3));
  });
});

describe('live re-estimate on edit', () => {
  it('updates the mm display and toggles the over-budget badge for 6mm', () => {
    openWith([{ mpn: 'X', lcsc: 'C1', description: 'a', primary_vendor_id: 'v_lcsc' }], '6mm');
    const row = rows()[0];
    const cell = editCells(row)[0];
    expect(row.querySelector('.label-badge')).toBeNull();

    // Type a string long enough to exceed the 6mm budget (115mm).
    const longText = 'M'.repeat(200); // wide chars, comfortably over 115mm
    cell.textContent = longText;
    cell.dispatchEvent(new Event('input'));

    const est = estimateWidthMm(longText, CFG.tape6.font_pt, CFG);
    expect(est).toBeGreaterThan(CFG.tape6.budget_mm);
    expect(row.querySelector('.label-mm').textContent).toBe(est.toFixed(1) + ' mm');
    expect(row.querySelector('.label-badge-red')).not.toBeNull();
  });

  it('clearing an over-budget edit removes the badge', () => {
    openWith([{ mpn: 'X', lcsc: 'C1', description: 'a', primary_vendor_id: 'v_lcsc' }], '6mm');
    const cell = editCells(rows()[0])[0];
    cell.textContent = 'M'.repeat(200);
    cell.dispatchEvent(new Event('input'));
    expect(rows()[0].querySelector('.label-badge-red')).not.toBeNull();
    cell.textContent = 'ok';
    cell.dispatchEvent(new Event('input'));
    expect(rows()[0].querySelector('.label-badge')).toBeNull();
  });
});

describe('live re-estimate on edit — 12mm badge thresholds', () => {
  // With the stubbed CFG:
  //   preferred_mm = 30, budget_mm = 40, font_pt = 10, calibration_k = 0.085
  //   default char width = 0.6  →  0.6 * 10 * 0.085 = 0.51 mm/char
  //   wide char (M)      width = 0.95 →  0.95 * 10 * 0.085 ≈ 0.8075 mm/char
  //
  //   To exceed preferred_mm (30) without exceeding budget_mm (40):
  //     40 wide chars  → ~32.3 mm  (30 < 32.3 < 40) → AMBER
  //   To exceed budget_mm (40):
  //     50 wide chars  → ~40.375 mm  (> 40) → RED
  //   Short text that falls below preferred_mm:
  //     5 wide chars   → ~4 mm → no badge

  function singleItem12mm() {
    return [{ mpn: 'X', lcsc: 'C1', description: 'a', primary_vendor_id: 'v_lcsc' }];
  }

  it('shows AMBER badge when estimated width exceeds preferred_mm but not budget_mm', () => {
    openWith(singleItem12mm(), '12mm');
    const row = rows()[0];
    const cell = editCells(row)[0]; // Line 1 cell
    expect(row.querySelector('.label-badge')).toBeNull();

    // 40 wide chars → ~32.3 mm, which is > preferred_mm (30) but < budget_mm (40).
    const amberText = 'M'.repeat(40);
    cell.textContent = amberText;
    cell.dispatchEvent(new Event('input'));

    const est = estimateWidthMm(amberText, CFG.tape12.font_pt, CFG);
    expect(est).toBeGreaterThan(CFG.tape12.preferred_mm);
    expect(est).toBeLessThan(CFG.tape12.budget_mm);
    expect(row.querySelector('.label-mm').textContent).toBe(est.toFixed(1) + ' mm');
    expect(row.querySelector('.label-badge-amber')).not.toBeNull();
    expect(row.querySelector('.label-badge-red')).toBeNull();
  });

  it('shows RED badge when estimated width exceeds budget_mm', () => {
    openWith(singleItem12mm(), '12mm');
    const row = rows()[0];
    const cell = editCells(row)[0];

    // 50 wide chars → ~40.375 mm, which is > budget_mm (40).
    const redText = 'M'.repeat(50);
    cell.textContent = redText;
    cell.dispatchEvent(new Event('input'));

    const est = estimateWidthMm(redText, CFG.tape12.font_pt, CFG);
    expect(est).toBeGreaterThan(CFG.tape12.budget_mm);
    expect(row.querySelector('.label-badge-red')).not.toBeNull();
    expect(row.querySelector('.label-badge-amber')).toBeNull();
  });

  it('clears the badge when width falls back below preferred_mm', () => {
    openWith(singleItem12mm(), '12mm');
    const row = rows()[0];
    const cell = editCells(row)[0];

    // First push over budget.
    cell.textContent = 'M'.repeat(50);
    cell.dispatchEvent(new Event('input'));
    expect(row.querySelector('.label-badge-red')).not.toBeNull();

    // Now bring it back down below preferred_mm (5 wide chars ≈ 4 mm).
    cell.textContent = 'M'.repeat(5);
    cell.dispatchEvent(new Event('input'));

    const est = estimateWidthMm('M'.repeat(5), CFG.tape12.font_pt, CFG);
    expect(est).toBeLessThan(CFG.tape12.preferred_mm);
    expect(row.querySelector('.label-badge')).toBeNull();
  });
});

describe('tape toggle', () => {
  it('switching to 12mm rebuilds the model (three cells per row)', () => {
    openWith(sampleItems(), '6mm');
    expect(editCells(rows()[0])).toHaveLength(1);
    const r12 = document.querySelector('input[name="label-export-tape"][value="12mm"]');
    r12.checked = true;
    r12.dispatchEvent(new Event('change', { bubbles: true }));
    expect(editCells(rows()[0])).toHaveLength(3);
  });
});

describe('export', () => {
  it('calls save_file_dialog once per distributor group with edited content', async () => {
    openWith(sampleItems(), '6mm');
    // Edit the first label's text.
    const cell = editCells(rows()[0])[0];
    cell.textContent = 'EDITED-LABEL';
    cell.dispatchEvent(new Event('input'));

    await doExport();

    // Two distributor groups → two save calls.
    expect(api).toHaveBeenCalledTimes(2);
    const calls = api.mock.calls;
    calls.forEach((c) => expect(c[0]).toBe('save_file_dialog'));
    // Default names carry tape + vendor short.
    const names = calls.map((c) => c[2]);
    expect(names).toContain('labels_6mm_lcsc.csv');
    expect(names).toContain('labels_6mm_digikey.csv');
    // The edited value flows into the lcsc CSV.
    const lcscCsv = calls.find((c) => c[2] === 'labels_6mm_lcsc.csv')[1];
    expect(lcscCsv).toContain('EDITED-LABEL');
    // Modal closed and a summary toast shown.
    expect(document.getElementById('label-export-modal').classList.contains('hidden')).toBe(true);
    expect(showToast).toHaveBeenCalledWith(expect.stringContaining('Exported 2'));
  });

  it('skips a file when the save dialog is cancelled (null), without throwing', async () => {
    openWith(sampleItems(), '6mm');
    // First save succeeds, second returns null (user cancelled).
    api.mockResolvedValueOnce({ path: 'C:/out/lcsc.csv' });
    api.mockResolvedValueOnce(null);
    await expect(doExport()).resolves.toBeUndefined();
    expect(api).toHaveBeenCalledTimes(2);
    expect(showToast).toHaveBeenCalledWith(expect.stringContaining('Exported 1'));
  });

  it('logs an error and continues when the api call throws', async () => {
    openWith(sampleItems(), '6mm');
    api.mockRejectedValueOnce(new Error('boom'));
    api.mockResolvedValueOnce({ path: 'C:/out/digikey.csv' });
    await doExport();
    expect(AppLog.error).toHaveBeenCalled();
    expect(showToast).toHaveBeenCalledWith(expect.stringContaining('Exported 1'));
  });
});
