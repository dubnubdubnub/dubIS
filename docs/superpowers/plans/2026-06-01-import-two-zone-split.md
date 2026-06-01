# Two-zone import split — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Split the import panel into two side-by-side flows — **CSV/TSV/TXT/XLS** (inline column-mapping + staging, plus blank-PO templates and manual "+ add row") and **Image/PDF/Phone** (template dropdown defaulting to Generic → existing OCR review modal, plus "Scan with phone"). Remove the ★ Direct-from-mfg button and the standalone editor as an *import* entry. Improve the Generic OCR heuristic and its labeling.

**Architecture:** Reuse the existing OCR-overlay machinery. Add two thin entry points to the mfg-direct module (`openOcrImport`, `startPhoneScan`) that open the overlay/scan modal directly (no editor). The import panel owns a new two-zone renderer and routes by file type / button. `editPO()` keeps using `renderEditor` (untouched). Backend: smarter `generic` profile in `distributor_profiles.py`.

**Tech stack:** vanilla JS ES modules (no build), Python, vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-06-01-import-two-zone-split-design.md`

---

## File structure

| File | Change |
|------|--------|
| `distributor_profiles.py` | Smarter `generic` free-form heuristic (+ tests) |
| `js/import/mfg-direct/mfg-direct-panel.js` | Export `openOcrImport(mountEl,file,template)` + `startPhoneScan(mountEl,template)`; remove `startDirectFlow` (unused after ★ removal); keep `editPO`/`renderEditor`/scan modal/overlay/`importPO`/`registerScanHandler` |
| `js/import/import-renderer.js` | New two-zone `renderDropZone` (CSV zone + image zone w/ template `<select>` + scan button); remove ★ button |
| `js/import/import-panel.js` | Wire both zones; CSV→`handleImportFile`, image/PDF→`openOcrImport`, scan→`startPhoneScan`; "+ add row manually"; remove the `direct` branch + ★-frame SVG (`setupDropZoneFrame`) |
| `css/panels/import.css` (or wherever import drop-zone CSS lives) | Two-zone layout, reusing tokens; pass layout-token guard |
| Tests | vitest (routing/template-default/manual-row), python (generic heuristic), Playwright (entry-point updates) |

---

## Task 1: Smarter generic OCR heuristic (backend)

**Files:** Modify `distributor_profiles.py`; Test `tests/python/test_distributor_profiles.py` (extend).

Context: the `generic` profile currently falls back to `mfg_direct_import._heuristic_parse_lines` (a single MPN-Mfg-Qty-Price regex). Free-form manufacturer invoices vary; make extraction more robust where there's no distributor-PN anchor.

- [ ] **Step 1: Read** `distributor_profiles.py` (`parse_with_template`, the `generic` profile/fallback, `DistributorProfile.parse`, `_TRAILING_QTY_PRICE`, helpers) and `mfg_direct_import._heuristic_parse_lines` to see the current generic path.

- [ ] **Step 2: Write failing tests** in `tests/python/test_distributor_profiles.py` — representative free-form lines the current heuristic misses, e.g.:
```python
class TestGenericFreeform:
    def _rows(self, text):
        import distributor_profiles
        return distributor_profiles.parse_with_template("generic", text)

    def test_mpn_not_first_token(self):
        # "1  KT-0603G  Kingbright  4,000  0.0220"
        rows = self._rows("1  KT-0603G  Kingbright  4000  0.0220")
        assert any(r["mpn"] == "KT-0603G" for r in rows)
        r = next(r for r in rows if r["mpn"] == "KT-0603G")
        assert r["quantity"] == 4000
        assert abs(r["unit_price"] - 0.022) < 1e-6

    def test_integer_unit_price(self):
        rows = self._rows("WS2812B-V5  Worldsemi  1000  6")
        assert rows and rows[0]["quantity"] == 1000 and rows[0]["unit_price"] == 6.0

    def test_label_noise_tolerated(self):
        rows = self._rows("Part: STM32F103C8T6  Qty 50  Unit Price $3.10")
        assert any("STM32F103C8T6" in r["mpn"] for r in rows)
```
(Adjust expected values to whatever the agreed heuristic should yield; keep them realistic and assert the behaviors the spec calls out: MPN not-first-token, integer prices, label noise.)

- [ ] **Step 3: Run** `python -m pytest tests/python/test_distributor_profiles.py::TestGenericFreeform -q` → FAIL.

- [ ] **Step 4: Improve the `generic` heuristic** so those pass without regressing existing distributor/generic tests. Approach (keep heuristic, documented): isolate a trailing `<int qty> <int-or-decimal price>` region first (reuse/extend `_TRAILING_QTY_PRICE` to allow integer prices and optional `$`/labels), strip leading index numbers, take the first MPN-like token (`[A-Z0-9][A-Z0-9._/+\-]{2,}`) as `mpn`, and a following capitalized word as `manufacturer` when present. Do NOT pin the SKU/MPN to a single position. Prefer no-extraction over a wrong one.

- [ ] **Step 5: Run** the full file: `python -m pytest tests/python/test_distributor_profiles.py tests/python/test_mfg_direct_import.py -q` → all green (incl. the tesseract-free mock tests). `ruff check .`.

- [ ] **Step 6: Commit** `feat(import): smarter generic free-form OCR heuristic`.

---

## Task 2: mfg-direct entry points (`openOcrImport`, `startPhoneScan`)

**Files:** Modify `js/import/mfg-direct/mfg-direct-panel.js`.

Goal: expose two thin entry points the import panel can call to open the OCR modal / scan modal **without** rendering the old editor, reusing existing logic. Remove the now-unused `startDirectFlow` (the editor-as-import-entry) but KEEP `editPO`, `renderEditor`, `rerender`, `bindEvents`, the scan modal (`openScanModal`/`bindScanModal`), `importPO`, `scanReceived`, `registerScanHandler`, and `vendorPicker` — `editPO` still uses the editor.

- [ ] **Step 1: Add `openOcrImport`** (refactor the OCR branch out of `handleSourceFile`):
```js
/** Open the OCR review modal for a dropped/selected image or PDF (no editor). */
export async function openOcrImport(mountElement, file, template = 'generic') {
  mountEl = mountElement || document.getElementById('import-body');
  const b64 = await _fileToB64(file);
  try {
    const payload = await apiMfgDirect.ocrOverlayB64(b64, file.name, template);
    if (payload && payload.pages && payload.pages.length) {
      _resetForImport(template);
      openOverlay(payload, {
        onConfirm: (rows, vendor) => {
          state.lineItems = rows;
          state.vendor = vendor;
          state.sourceFile = { name: file.name, bytes: b64 };
          importPO();
        },
      });
      return;
    }
    showToast('No text found in that file — try a clearer photo or a CSV');
    AppLog.warn('ocr_overlay_b64 returned no pages');
  } catch (exc) {
    AppLog.error('OCR import failed: ' + exc);
    showToast('OCR failed — see log');
  }
}
```
with helpers:
```js
function _fileToB64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(typeof r.result === 'string' ? r.result.split(',')[1] : '');
    r.onerror = () => reject(new Error('read failed'));
    r.readAsDataURL(file);
  });
}
function _resetForImport(template) {
  state.active = true; state.editingPoId = null;
  state.vendor = { id: '', name: '', url: '', favicon_path: '', icon: '', type: '' };
  state.lineItems = []; state.sourceFile = null;
  state.scanTemplate = template || 'generic';
}
```

- [ ] **Step 2: Add `startPhoneScan`** (wraps `startScanSession` with a template + mount):
```js
export async function startPhoneScan(mountElement, template = 'generic') {
  mountEl = mountElement || document.getElementById('import-body');
  _resetForImport(template);
  const session = await apiMfgDirect.startScanSession(template);
  if (!session || !session.urls || !session.urls.length) {
    AppLog.warn('start_scan_session returned no URLs'); showToast('Could not start scan session'); return;
  }
  openScanModal(session);
}
```
The existing `scanReceived` already opens the overlay when `payload.pages` is present and calls `importPO` on confirm — keep it. Its `if (!state.active) startDirectFlow(...)` line must change (startDirectFlow is being removed): replace with `if (!state.active) { mountEl = mountEl || document.getElementById('import-body'); _resetForImport(payload.template || 'generic'); }`.

- [ ] **Step 3: Remove** `export function startDirectFlow` and its body. Confirm nothing else references it (grep — only `import-panel.js`'s `direct` branch did, removed in Task 3). Keep `handleSourceFile` ONLY if still used by the editor's drop zone for `editPO`; since `editPO` shows the editor which has `#mfg-source-drop`, the editor's own drop still routes through `bindEvents`→`handleSourceFile`. **Keep `handleSourceFile`** (it serves the editor used by `editPO`), but it can stay as-is. (Do not delete machinery `editPO` depends on.)

- [ ] **Step 4: Verify** `npx eslint js/ && npx tsc --noEmit && npx vitest run` (existing tests may reference removed exports — if a unit test imports `startDirectFlow`, update it in Task 4). Commit `refactor(import): openOcrImport + startPhoneScan entry points`.

---

## Task 3: Two-zone import panel renderer + wiring

**Files:** Modify `js/import/import-renderer.js`, `js/import/import-panel.js`, import CSS, `index.html` (if a new CSS file).

- [ ] **Step 1: New `renderDropZone(templates)`** in `import-renderer.js` — two zones side by side; remove the ★ button. Structure (reuse existing classes/ids where possible; `#import-drop-zone` + `#import-file-input` stay for the CSV zone so existing wiring/tests keep working):
```js
export function renderDropZone(templates) {
  const ocrTemplates = { generic: 'Generic — direct from mfg', lcsc: 'LCSC', digikey: 'DigiKey', mouser: 'Mouser', pololu: 'Pololu' };
  return `
    <div class="import-section">
      <div class="import-zones">
        <div class="drop-zone import-zone-csv" id="import-drop-zone">
          <p>Drop a purchase CSV here</p>
          <div class="hint">LCSC orders, cart exports, packing lists, DigiKey, Pololu, Mouser</div>
          <input type="file" id="import-file-input" accept=".csv,.tsv,.txt,.xls">
          <div class="new-po-row" id="new-po-row">
            <span class="new-po-label">or create blank PO:</span>
            ${Object.entries(templates).map(([k, t]) => `<button class="new-po-btn" data-template="${k}">${t.label}</button>`).join('')}
            <button class="new-po-btn" id="import-add-row">+ add row manually</button>
          </div>
        </div>
        <div class="drop-zone import-zone-ocr" id="import-ocr-zone">
          <label class="ocr-template-label">Template:
            <select id="import-ocr-template">
              ${Object.entries(ocrTemplates).map(([k, label]) => `<option value="${k}"${k === 'generic' ? ' selected' : ''}>${label}</option>`).join('')}
            </select>
          </label>
          <div class="hint">Generic = a manufacturer invoice with no distributor packing list</div>
          <p>Drop an image / PDF here</p>
          <input type="file" id="import-ocr-input" accept=".png,.jpg,.jpeg,.pdf">
          <button class="new-po-btn" id="import-scan-btn">📷 Scan with phone</button>
        </div>
      </div>
      <div id="import-mapper" class="hidden"></div>
    </div>
  `;
}
```

- [ ] **Step 2: Wire it in `import-panel.js`** `init()`:
  - Keep `setupDropZone("import-drop-zone", "import-file-input", browseImportFile, handleImportFile)` for the CSV zone.
  - Add an OCR-zone setup: drop/click on `#import-ocr-zone`/`#import-ocr-input` → read the file and call `import('./mfg-direct/mfg-direct-panel.js').then(m => m.openOcrImport(body, file, ocrTemplate()))` where `ocrTemplate()` reads `#import-ocr-template`. (Use the existing `setupDropZone` helper if it fits; otherwise wire `ondrop`/`onchange` mirroring it.)
  - `#import-scan-btn` → `import('./mfg-direct/mfg-direct-panel.js').then(m => m.startPhoneScan(body, ocrTemplate()))`.
  - `#import-add-row` → seed inline manual entry: set `parsedHeaders` to the Generic template headers (from `PO_TEMPLATES.generic.headers`), `parsedRows = [emptyRowForHeaders]`, default `columnMapping` (identity), then `renderMapper()` so the user types into the staging table and imports via the existing path.
  - **Remove** the `data-template === 'direct'` branch and the `★`/`has-direct-frame` handling, and the `setupDropZoneFrame()` call + its SVG helper functions (no longer needed). Remove `has-direct-frame` from the other `zone.innerHTML` rebuilds (undo/redo handlers) and update those `accept` strings if needed.
  - Keep `createNewPO` for the blank-PO template buttons.

- [ ] **Step 3: CSS** — add `.import-zones { display:flex; gap: <token>; }` and `.import-zone-csv`/`.import-zone-ocr { flex:1; }` styling, reusing existing drop-zone styles + tokens. Static dims as `--*` tokens in `css/tokens.css`. Run `python scripts/check-layout-tokens.py --check`.

- [ ] **Step 4: Verify** `npx eslint js/ && npx tsc --noEmit && npx vitest run`. Commit `feat(import): two-zone drop area (CSV | image/PDF/phone), drop ★ button`.

---

## Task 4: Tests — vitest + Playwright entry-point updates

**Files:** vitest under `tests/js/`; Playwright `tests/js/e2e/`.

- [ ] **Step 1: vitest** — pure-logic tests for: extension→route decision (`.csv`→CSV inline, `.png/.jpg/.pdf`→OCR), `#import-ocr-template` default = `generic`, "+ add row" seeds one editable staging row with the Generic headers. Factor any new pure helper (e.g. `isOcrFile(name)`) so it's unit-testable. Fix any existing unit test that imported the removed `startDirectFlow`.

- [ ] **Step 2: Playwright** — update specs to the new entry points (realistic interactions only; no dispatchEvent/force):
  - Remove assumptions about the `★ Direct from mfg` button / `startDirectFlow`.
  - New/updated `import-two-zone.spec.mjs`: CSV drop on `#import-drop-zone` → inline staging (no modal); image `setInputFiles` on `#import-ocr-input` → OCR modal `#ocr-overlay` opens (reuse the mocked `ocr_overlay_b64` in `helpers.mjs`); `#import-ocr-template` defaults to "Generic"; `#import-scan-btn` → scan QR modal appears; `#import-add-row` → an editable staging row appears.
  - Update `ocr-overlay.spec.mjs` / `mfg-direct.spec.mjs` / `scan-import.spec.mjs`: reach the overlay via the new import-panel OCR zone instead of the ★/editor flow (or via `openOcrImport`/`window._scanReceived` directly where they already do). Ensure `editPO` coverage (if any) still works since the editor remains.
  - Do NOT weaken `sticky-buttons`/`resize-visibility`.

- [ ] **Step 3: Run full suite** — `ruff check . && pytest tests/python/ -q && npx eslint js/ && npx tsc --noEmit && npx vitest run && npx playwright test --project=functional --project=quality`. Regenerate `docs/code-map.md` (`python scripts/gen-code-map.py`) + `python scripts/check-manifests.py` + layout guard; commit any code-map change.

- [ ] **Step 4: Commit** `test(import): cover two-zone split + update entry-point specs`.

---

## Final verification (before PR)

```bash
ruff check .
python -m pytest tests/python/ -q
npx eslint js/ && npx tsc --noEmit && npx vitest run
npx playwright test
python scripts/gen-code-map.py && git add docs/code-map.md && git commit -m "chore(code-map): regenerate" || true
python scripts/check-layout-tokens.py --check
python scripts/check-manifests.py
```

Then push + PR via `bash scripts/push-pr.sh`, watch CI to green, merge.

## Gotchas (from prior sessions — see memory)

- New runtime dep → also add to `requirements-dev.txt` (CI installs only that). (No new dep expected here.)
- Regenerate `docs/code-map.md` after module changes (CI verifies it).
- Layout-token guard: no raw px in `css/` layout props — use `--*` tokens; its ignore list is line-anchored.
- OCR tests run on CI macOS (tesseract present) — render fixtures with a sized font; pair binary-guarded tests with a `monkeypatch` mock test.
- `editPO` depends on `renderEditor`/the editor machinery — do NOT delete it when removing the ★ import entry.
- Rebase onto latest `origin/main` before merge (it moves fast).
