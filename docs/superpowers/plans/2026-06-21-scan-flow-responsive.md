# Responsive Unified Scan / OCR Flow — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make image import (drag-drop, click-to-browse, phone) give instant feedback, share one downstream flow, support multiple images, highlight using the model that detected each row, and let the template be switched after upload (re-parsing cached OCR + auto-prefilling the vendor).

**Architecture:** A single JS router (`beginScanImport` → `routeScanResult`) in `mfg-direct-panel.js` opens an immediate "Reading…" shell before any OCR, streams per-file OCR results in, then routes 1→overlay / 2+→grouping editor — the same path the phone already uses. The Python OCR pipeline tags each prefill row with the backend that produced it (`vlm`/`grid`/`flat`) and a pixel `bbox` for VLM rows; the overlay highlights a row via its own bbox or, when null, by fuzzy-matching the row text to Tesseract tokens. A shared `template-switch.js` module re-parses cached OCR text and auto-selects the matching vendor when the template changes.

**Tech Stack:** Python 3 (pytesseract, Pillow, OpenCV, urllib→Ollama), vanilla ES modules (no framework/build), Vitest (JS unit), Playwright (E2E), pytest (Python).

## Global Constraints

- Prefer throwing errors over silently failing; use `AppLog.warn`/`AppLog.error` over silent catches. (CLAUDE.md)
- Never use `pytest.skip`/`importorskip`/`mark.skip`; add missing deps to `requirements-dev.txt`. (CLAUDE.md)
- Never weaken `sticky-buttons.spec.mjs` or `resize-visibility.spec.mjs`; fix CSS if they fail. (CLAUDE.md)
- Playwright tests: realistic interactions only — no `dispatchEvent`, no `force:true`. (user memory)
- After backend row-shape changes run `python scripts/generate-test-fixtures.py` before JS tests. (CLAUDE.md)
- JS gate: `npx eslint js/ && npx tsc --noEmit && npx vitest run`. Python gate: `ruff check . && pytest tests/python/ -v`.
- VLM backend stays local-only (Ollama) and self-gating: returns `None` on any unavailability so GPU-less nodes/CI are unaffected. (`vlm_extract.py`)
- Distributor templates are exactly: `generic`, `lcsc`, `digikey`, `mouser`, `pololu`. (`distributor_profiles._make_profiles`)
- Line-item dict shape is shared across backends: `{mpn, manufacturer, package, description, quantity, unit_price, distributor, distributor_pn}`; new fields added by this plan are `_backend` and `bbox`.

---

## Task 1: Backend — tag VLM rows with `_backend` + parse `bbox`

**Files:**
- Modify: `vlm_extract.py` (`_PROMPT`, `_to_line_item`, add `_parse_bbox`)
- Test: `tests/python/test_vlm_extract.py` (create if absent)

**Interfaces:**
- Consumes: nothing new.
- Produces: `vlm_extract._to_line_item(raw: dict, template: str, page_w: int, page_h: int) -> dict | None` now returns rows including `"_backend": "vlm"` and `"bbox": [x,y,w,h] | None` (pixel ints). `vlm_extract.extract_line_items(image_bytes, template, page_w, page_h)` gains the two size args (default `0,0` → bbox stays `None`).
- `vlm_extract._parse_bbox(raw_bbox, page_w, page_h) -> list[int] | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/python/test_vlm_extract.py
import vlm_extract


def test_to_line_item_tags_backend_and_parses_bbox():
    raw = {"distributor_pn": "C12345", "mfr_pn": "RC0402", "description": "10k",
           "qty": 100, "bbox": [100, 200, 300, 250]}  # 0..1000 normalized
    item = vlm_extract._to_line_item(raw, "lcsc", 1000, 2000)
    assert item["_backend"] == "vlm"
    assert item["distributor_pn"] == "C12345"
    # 0..1000 grid -> pixels: x=100/1000*1000=100, y=200/1000*2000=400,
    # w=(300-100)/1000*1000=200, h=(250-200)/1000*2000=100
    assert item["bbox"] == [100, 400, 200, 100]


def test_to_line_item_missing_bbox_is_none():
    raw = {"mfr_pn": "RC0402", "qty": 5}
    item = vlm_extract._to_line_item(raw, "generic", 1000, 1000)
    assert item["_backend"] == "vlm"
    assert item["bbox"] is None


def test_to_line_item_malformed_bbox_is_none_not_raise():
    raw = {"mfr_pn": "RC0402", "qty": 5, "bbox": ["x", 1, 2]}
    item = vlm_extract._to_line_item(raw, "generic", 1000, 1000)
    assert item["bbox"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/python/test_vlm_extract.py -v`
Expected: FAIL — `_to_line_item()` currently takes `(raw, template)` and returns no `_backend`/`bbox`.

- [ ] **Step 3: Implement bbox parsing + new fields**

In `vlm_extract.py`, the model emits a `[x, y, w, h]`-style box on the Qwen 0–1000 normalized grid where the four numbers are `[x0, y0, x1, y1]` corners. Add:

```python
def _parse_bbox(raw_bbox, page_w: int, page_h: int):
    """Convert a model bbox (0..1000 grid, [x0,y0,x1,y1]) to pixel [x,y,w,h].

    Returns None for anything malformed/out-of-range or when the page size is
    unknown — the caller falls back to Tesseract-token matching for highlight.
    """
    if not page_w or not page_h or not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in raw_bbox)
    except (TypeError, ValueError):
        return None
    if x1 < x0 or y1 < y0:
        return None
    px = int(max(0.0, min(1000.0, x0)) / 1000.0 * page_w)
    py = int(max(0.0, min(1000.0, y0)) / 1000.0 * page_h)
    pw = int(max(0.0, min(1000.0, x1 - x0)) / 1000.0 * page_w)
    ph = int(max(0.0, min(1000.0, y1 - y0)) / 1000.0 * page_h)
    if pw <= 0 or ph <= 0:
        return None
    return [px, py, pw, ph]
```

Change `_to_line_item` signature and body:

```python
def _to_line_item(raw: dict, template: str, page_w: int = 0, page_h: int = 0):
    distributor_pn = str(raw.get("distributor_pn") or "").strip()
    mpn = str(raw.get("mfr_pn") or raw.get("mpn") or "").strip()
    description = str(raw.get("description") or "").strip()
    quantity = _to_qty(raw.get("qty") or raw.get("quantity"))
    if not distributor_pn and not mpn:
        return None
    distributor = template if template in _PN_COLUMN_TEMPLATES else "generic"
    if distributor == "generic":
        distributor_pn = ""
    return {
        "mpn": mpn,
        "manufacturer": "",
        "package": "",
        "description": description,
        "quantity": quantity,
        "unit_price": 0.0,
        "distributor": distributor,
        "distributor_pn": distributor_pn,
        "_backend": "vlm",
        "bbox": _parse_bbox(raw.get("bbox"), page_w, page_h),
    }
```

Thread sizes through `_parse_response` and `extract_line_items`/`_extract`:

```python
def _parse_response(response_text: str, template: str, page_w: int = 0, page_h: int = 0):
    ...
        item = _to_line_item(raw, template, page_w, page_h)
    ...

def extract_line_items(image_bytes: bytes, template: str = "generic",
                       page_w: int = 0, page_h: int = 0):
    if _disabled() or not image_bytes:
        return None
    try:
        return _extract(image_bytes, template, page_w, page_h)
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        logger.warning("VLM extraction failed, falling back: %s", exc)
        return None

def _extract(image_bytes: bytes, template: str, page_w: int = 0, page_h: int = 0):
    ...
    rows = _parse_response(body.get("response", ""), template, page_w, page_h)
    return rows or None
```

Add the bbox request to `_PROMPT` (append before the final "Output only the JSON object."):

```python
    "\"bbox\": the bounding box of the row in the image as [x0, y0, x1, y1] "
    "integers on a 0-1000 normalized grid (x right, y down). "
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/python/test_vlm_extract.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add vlm_extract.py tests/python/test_vlm_extract.py
git commit -m "feat(ocr): VLM rows carry _backend + parsed pixel bbox"
```

---

## Task 2: Backend — template-aware VLM prompt

**Files:**
- Modify: `vlm_extract.py` (add `_prompt_for`, use it in `_extract`)
- Test: `tests/python/test_vlm_extract.py`

**Interfaces:**
- Produces: `vlm_extract._prompt_for(template: str) -> str` — base prompt plus a distributor hint line for distributor templates; base prompt unchanged for `generic`.

- [ ] **Step 1: Write the failing test**

```python
def test_prompt_for_includes_distributor_hint():
    p = vlm_extract._prompt_for("lcsc")
    assert "LCSC" in p
    assert "C" in p  # mentions the C<digits> PN format
    assert "bbox" in p  # still asks for the box

def test_prompt_for_generic_has_no_distributor_hint():
    p = vlm_extract._prompt_for("generic")
    assert "LCSC packing list" not in p
    assert "DigiKey packing list" not in p
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/python/test_vlm_extract.py::test_prompt_for_includes_distributor_hint -v`
Expected: FAIL — `_prompt_for` not defined.

- [ ] **Step 3: Implement**

```python
_TEMPLATE_HINTS = {
    "lcsc": "This is an LCSC packing list; its catalogue part numbers look like "
            "C followed by digits (e.g. C12345). Put them in distributor_pn.",
    "digikey": "This is a DigiKey packing list; its catalogue part numbers "
               "usually end in -ND/-CT/-DKR. Put them in distributor_pn.",
    "mouser": "This is a Mouser packing list; its catalogue part numbers look "
              "like <digits>-<mfr part>. Put them in distributor_pn.",
    "pololu": "This is a Pololu packing list; its catalogue part numbers are "
              "bare numbers. Put them in distributor_pn.",
}


def _prompt_for(template: str) -> str:
    hint = _TEMPLATE_HINTS.get((template or "").strip().lower())
    return f"{_PROMPT}\n{hint}" if hint else _PROMPT
```

In `_extract`, replace `"prompt": _PROMPT,` with `"prompt": _prompt_for(template),`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/python/test_vlm_extract.py -v`
Expected: PASS (5 tests total).

- [ ] **Step 5: Commit**

```bash
git add vlm_extract.py tests/python/test_vlm_extract.py
git commit -m "feat(ocr): template-aware VLM prompt (distributor hint)"
```

---

## Task 3: Backend — tag grid/flat rows with `_backend` + null `bbox`; pass page size to VLM

**Files:**
- Modify: `ocr_layout.py` (`extract_pages`)
- Test: `tests/python/test_ocr_layout_backend.py` (create)

**Interfaces:**
- Consumes: `vlm_extract.extract_line_items(image_bytes, template, page_w, page_h)` (Task 1).
- Produces: every dict in `extract_pages(...)["prefill_rows"]` carries `_backend` (`"vlm"|"grid"|"flat"`) and `bbox` (list or `None`). Grid/flat rows always get `bbox=None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/python/test_ocr_layout_backend.py
import ocr_layout


def test_tag_rows_sets_backend_and_null_bbox():
    rows = [{"mpn": "A", "quantity": 1}, {"mpn": "B", "quantity": 2}]
    out = ocr_layout._tag_rows(rows, "grid")
    assert all(r["_backend"] == "grid" for r in out)
    assert all(r["bbox"] is None for r in out)


def test_tag_rows_preserves_existing_bbox_for_vlm():
    rows = [{"mpn": "A", "_backend": "vlm", "bbox": [1, 2, 3, 4]}]
    out = ocr_layout._tag_rows(rows, "vlm")
    assert out[0]["bbox"] == [1, 2, 3, 4]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/python/test_ocr_layout_backend.py -v`
Expected: FAIL — `_tag_rows` not defined.

- [ ] **Step 3: Implement**

Add helper to `ocr_layout.py`:

```python
def _tag_rows(rows, backend: str):
    """Stamp _backend on each row; ensure a bbox key exists (None if absent)."""
    for r in rows:
        r["_backend"] = r.get("_backend") or backend
        r.setdefault("bbox", None)
    return rows
```

In `extract_pages`, pass page size to the VLM and tag every path. Replace the VLM block and the grid/flat return:

```python
    page_w = raster[0][1] if raster else 0
    page_h = raster[0][2] if raster else 0
    import vlm_extract
    vlm_rows = (vlm_extract.extract_line_items(raster[0][0], template, page_w, page_h)
                if raster else None)
    if vlm_rows:
        return {"pages": pages, "prefill_rows": _tag_rows(vlm_rows, "vlm"),
                "template": template}

    import ocr_table
    grid_rows = (ocr_table.extract_line_items(raster[0][0], template) if raster else None) or []
    full_text = "\n".join(ln["text"] for pg in pages for ln in pg["lines"])
    flat_rows = distributor_profiles.parse_with_template(template, full_text) or []
    if len(grid_rows) >= len(flat_rows):
        prefill_rows = _tag_rows(grid_rows, "grid")
    else:
        prefill_rows = _tag_rows(flat_rows, "flat")
    return {"pages": pages, "prefill_rows": prefill_rows, "template": template}
```

(Note: `raster` items are `(png, w, h)` tuples — see existing `pages = [extract_page(png) for (png, _w, _h) in raster]`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/python/test_ocr_layout_backend.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run full Python gate + regenerate fixtures**

```bash
ruff check . && pytest tests/python/ -v && python scripts/generate-test-fixtures.py
```
Expected: all pass; fixtures regenerated (commit any changed fixture JSON).

- [ ] **Step 6: Commit**

```bash
git add ocr_layout.py tests/python/test_ocr_layout_backend.py tests/fixtures/generated/
git commit -m "feat(ocr): tag grid/flat rows with _backend; feed page size to VLM"
```

---

## Task 4: Frontend — `multiple` file input + setupDropZone passes all files

**Files:**
- Modify: `js/import/import-renderer.js:47` (add `multiple`)
- Modify: `js/ui-helpers.js:57-79` (`setupDropZone` — optional multi mode)
- Modify: `js/import/import-panel.js:113-118` (OCR-zone callback)
- Test: `tests/js/setup-drop-zone.test.mjs` (create)

**Interfaces:**
- Produces: `setupDropZone(zoneId, inputId, onBrowse, onFile, { multi = false } = {})`. When `multi` is true, `onFile` receives a `File[]` (from both drop and input change); when false, a single `File` (unchanged — CSV zone keeps current behavior).

- [ ] **Step 1: Write the failing test**

```js
// tests/js/setup-drop-zone.test.mjs
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { setupDropZone } from '../../js/ui-helpers.js';

function makeFile(name) { return new File(['x'], name, { type: 'image/png' }); }

describe('setupDropZone multi mode', () => {
  beforeEach(() => {
    document.body.innerHTML =
      `<div id="z"><input id="i" type="file"></div>`;
  });

  it('passes an array of files on drop when multi', () => {
    const onFile = vi.fn();
    setupDropZone('z', 'i', () => {}, onFile, { multi: true });
    const dt = { files: [makeFile('a.png'), makeFile('b.png')] };
    const ev = new Event('drop'); ev.dataTransfer = dt;
    document.getElementById('z').dispatchEvent(ev);
    expect(Array.isArray(onFile.mock.calls[0][0])).toBe(true);
    expect(onFile.mock.calls[0][0]).toHaveLength(2);
  });

  it('passes a single File when not multi (default)', () => {
    const onFile = vi.fn();
    setupDropZone('z', 'i', () => {}, onFile);
    const dt = { files: [makeFile('a.png')] };
    const ev = new Event('drop'); ev.dataTransfer = dt;
    document.getElementById('z').dispatchEvent(ev);
    expect(onFile.mock.calls[0][0]).toBeInstanceOf(File);
  });
});
```

(This is a unit test of pure wiring logic — the no-`dispatchEvent` rule applies to Playwright E2E, not Vitest unit tests, where synthesizing a DOM event is the standard approach.)

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run tests/js/setup-drop-zone.test.mjs`
Expected: FAIL — multi branch not implemented (array assertion fails).

- [ ] **Step 3: Implement**

In `js/ui-helpers.js`, change `setupDropZone` to accept options and branch:

```js
export function setupDropZone(zoneId, inputId, onBrowse, onFile, { multi = false } = {}) {
  const zone = document.getElementById(zoneId);
  const input = document.getElementById(inputId);
  zone.addEventListener("click", (e) => {
    if (e.target instanceof Element && e.target.closest("input, select, option, label, button")) return;
    onBrowse();
  });
  zone.addEventListener("dragover", (e) => { e.preventDefault(); e.stopPropagation(); zone.classList.add("dragover"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault(); e.stopPropagation(); zone.classList.remove("dragover");
    const files = e.dataTransfer.files;
    if (files.length) onFile(multi ? Array.from(files) : files[0]);
  });
  input.addEventListener("change", () => {
    if (input.files.length) onFile(multi ? Array.from(input.files) : input.files[0]);
  });
}
```

In `js/import/import-renderer.js:47`, add `multiple`:

```html
<input type="file" id="import-ocr-input" accept=".png,.jpg,.jpeg,.pdf" multiple>
```

In `js/import/import-panel.js:113-118`, pass `multi: true` and an array:

```js
  setupDropZone(
    "import-ocr-zone",
    "import-ocr-input",
    () => document.getElementById("import-ocr-input").click(),
    (files) => import('./mfg-direct/mfg-direct-panel.js').then(m => m.beginScanImport(body, files, ocrTemplate())),
    { multi: true },
  );
```

(`beginScanImport` is created in Task 6; until then this import resolves but the call would be undefined — acceptable because the JS gate is module-load + type only, and the E2E that exercises it lands in Task 8.)

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run tests/js/setup-drop-zone.test.mjs`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add js/ui-helpers.js js/import/import-renderer.js js/import/import-panel.js tests/js/setup-drop-zone.test.mjs
git commit -m "feat(import): OCR drop zone accepts multiple files"
```

---

## Task 5: Frontend — immediate "Reading…" shell module

**Files:**
- Create: `js/import/mfg-direct/scan-shell.js`
- Test: `tests/js/scan-shell.test.mjs` (create)

**Interfaces:**
- Produces:
  - `openScanShell(items) -> void` — `items: [{ name }]`; renders a modal `#scan-shell-overlay` with one tile per item, each in "reading" state.
  - `markShellTile(index, status, detail) -> void` — `status: "done"|"error"`; updates tile `index`'s state and optional `detail` text (e.g. "5 rows").
  - `closeScanShell() -> void` — removes the overlay if present.

- [ ] **Step 1: Write the failing test**

```js
// tests/js/scan-shell.test.mjs
import { describe, it, expect, beforeEach } from 'vitest';
import { openScanShell, markShellTile, closeScanShell } from '../../js/import/mfg-direct/scan-shell.js';

describe('scan shell', () => {
  beforeEach(() => { document.body.innerHTML = ''; closeScanShell(); });

  it('renders one reading tile per item', () => {
    openScanShell([{ name: 'a.png' }, { name: 'b.png' }]);
    const overlay = document.getElementById('scan-shell-overlay');
    expect(overlay).toBeTruthy();
    expect(overlay.querySelectorAll('.scan-shell-tile').length).toBe(2);
    expect(overlay.querySelectorAll('.scan-shell-tile.reading').length).toBe(2);
  });

  it('marks a tile done with detail', () => {
    openScanShell([{ name: 'a.png' }]);
    markShellTile(0, 'done', '5 rows');
    const tile = document.querySelector('.scan-shell-tile');
    expect(tile.classList.contains('done')).toBe(true);
    expect(tile.classList.contains('reading')).toBe(false);
    expect(tile.textContent).toContain('5 rows');
  });

  it('closeScanShell removes the overlay', () => {
    openScanShell([{ name: 'a.png' }]);
    closeScanShell();
    expect(document.getElementById('scan-shell-overlay')).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run tests/js/scan-shell.test.mjs`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement**

```js
// js/import/mfg-direct/scan-shell.js
/* scan-shell.js — instant "Reading…" acknowledgement shown the moment an image
 * lands (drop / browse), before any OCR runs. One tile per file; each tile flips
 * to done/error as its OCR completes. Pure DOM (no api/store). */

import { escHtml } from '../../ui-helpers.js';

function _overlay() { return document.getElementById('scan-shell-overlay'); }

export function openScanShell(items) {
  closeScanShell();
  const tiles = (items || []).map((it, i) => `
    <div class="scan-shell-tile reading" data-idx="${i}">
      <span class="scan-shell-spinner" aria-hidden="true"></span>
      <span class="scan-shell-name">${escHtml(it.name || `Image ${i + 1}`)}</span>
      <span class="scan-shell-detail">Reading…</span>
    </div>`).join('');
  const overlay = document.createElement('div');
  overlay.id = 'scan-shell-overlay';
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `<div class="modal scan-shell-modal" role="status" aria-live="polite">
    <div class="modal-title">📸 Reading ${items.length} image${items.length === 1 ? '' : 's'}…</div>
    <div class="scan-shell-tiles">${tiles}</div>
  </div>`;
  document.body.appendChild(overlay);
}

export function markShellTile(index, status, detail) {
  const overlay = _overlay();
  if (!overlay) return;
  const tile = overlay.querySelector(`.scan-shell-tile[data-idx="${index}"]`);
  if (!tile) return;
  tile.classList.remove('reading');
  tile.classList.add(status === 'error' ? 'error' : 'done');
  const det = tile.querySelector('.scan-shell-detail');
  if (det) det.textContent = detail || (status === 'error' ? 'Failed' : 'Done');
}

export function closeScanShell() {
  const overlay = _overlay();
  if (overlay) overlay.remove();
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run tests/js/scan-shell.test.mjs`
Expected: PASS (3 tests).

- [ ] **Step 5: Add minimal CSS for the shell**

In `css/styles.css`, append (reusing existing `.modal-overlay`/`.modal`):

```css
.scan-shell-tiles { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.scan-shell-tile { display: flex; align-items: center; gap: 6px; padding: 6px 10px;
  border: 1px solid var(--border, #ccc); border-radius: 6px; min-width: 160px; }
.scan-shell-tile.reading .scan-shell-detail { opacity: 0.7; }
.scan-shell-tile.done { border-color: #2e7d32; }
.scan-shell-tile.error { border-color: #c62828; }
.scan-shell-spinner { width: 12px; height: 12px; border: 2px solid currentColor;
  border-top-color: transparent; border-radius: 50%; animation: scan-shell-spin 0.8s linear infinite; }
.scan-shell-tile:not(.reading) .scan-shell-spinner { display: none; }
@keyframes scan-shell-spin { to { transform: rotate(360deg); } }
```

- [ ] **Step 6: Commit**

```bash
git add js/import/mfg-direct/scan-shell.js tests/js/scan-shell.test.mjs css/styles.css
git commit -m "feat(scan): instant Reading… shell for dropped images"
```

---

## Task 6: Frontend — unified entry (`beginScanImport`) + shared router (`routeScanResult`)

**Files:**
- Modify: `js/import/mfg-direct/mfg-direct-panel.js` (add `beginScanImport`, `routeScanResult`; refactor `openOcrImport` + `scanReceived` to use them)
- Test: `tests/js/route-scan-result.test.mjs` (create)

**Interfaces:**
- Consumes: `openScanShell`/`markShellTile`/`closeScanShell` (Task 5); `openOverlay`, `openGroupingEditor`, `startImportQueue`, `apiMfgDirect.ocrOverlayB64`, `_fileToB64`.
- Produces (exported):
  - `beginScanImport(mountElement, files, template = 'generic') -> Promise<void>` — opens the shell immediately, OCRs each file sequentially (streaming tiles), then calls `routeScanResult`.
  - `routeScanResult(photos, groups, template, sourceHint)` — `photos.length > 1` → grouping editor; else → overlay. `photos[i] = { index, filename, image_b64, pages, prefill_rows }`.

- [ ] **Step 1: Write the failing test**

```js
// tests/js/route-scan-result.test.mjs
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run tests/js/route-scan-result.test.mjs`
Expected: FAIL — `routeScanResult` not exported.

- [ ] **Step 3: Implement `routeScanResult` + `beginScanImport`, refactor callers**

Add imports near the top of `mfg-direct-panel.js`:

```js
import { openScanShell, markShellTile, closeScanShell } from './scan-shell.js';
```

Add the router (mirrors the existing `scanReceived` branching, but reusable):

```js
/**
 * Shared downstream for every image source (drag/browse/phone). 1 photo →
 * overlay; 2+ → grouping editor. `photos[i]` is a per-photo OCR record:
 * { index, filename, image_b64, pages, prefill_rows }.
 */
export function routeScanResult(photos, groups, template, sourceHint) {
  if (!photos || !photos.length) {
    showToast('No text found — try a clearer photo or a CSV');
    return;
  }
  state.scanTemplate = template || state.scanTemplate;
  if (photos.length > 1) {
    openGroupingEditor(photos, groups, template || 'generic',
      (groupPayloads) => startImportQueue(groupPayloads));
    AppLog.info(`Scan: grouping editor for ${photos.length} photo(s)`);
    return;
  }
  const only = photos[0];
  openOverlay({ pages: only.pages, prefill_rows: only.prefill_rows, template },
    {
      onConfirm: (rows, vendor) => {
        state.lineItems = rows;
        state.vendor = vendor;
        state.sourceFile = sourceHint
          || { name: only.filename, bytes: only.image_b64 };
        importPO();
      },
    });
  AppLog.info(`Scan: overlay for ${only.filename} (${template || 'generic'})`);
}
```

Add the unified drag/browse entry (replaces `openOcrImport`'s body):

```js
/**
 * Unified entry for drag-drop AND click-to-browse. Opens the Reading… shell
 * IMMEDIATELY (before any OCR), OCRs each file sequentially while streaming the
 * result into its tile, then routes via routeScanResult.
 */
export async function beginScanImport(mountElement, files, template = 'generic') {
  const list = Array.isArray(files) ? files : (files ? [files] : []);
  if (!list.length) return;
  _resetForImport(mountElement, template);
  openScanShell(list.map(f => ({ name: f.name })));

  // Surface a missing OCR engine before the heavier per-file loop.
  try {
    if ((await apiMfgDirect.ocrEngineAvailable()) === false) {
      closeScanShell();
      showToast('OCR engine not available — install Tesseract');
      AppLog.warn('ocr_engine_available returned false');
      return;
    }
  } catch (exc) {
    AppLog.warn('ocr_engine_available check failed: ' + exc);
  }

  const photos = [];
  for (let i = 0; i < list.length; i++) {
    const file = list[i];
    try {
      const b64 = await _fileToB64(file);
      const payload = await apiMfgDirect.ocrOverlayB64(b64, file.name, template);
      if (payload && payload.pages && payload.pages.length) {
        photos.push({ index: i, filename: file.name, image_b64: b64,
          pages: payload.pages, prefill_rows: payload.prefill_rows || [] });
        markShellTile(i, 'done', `${(payload.prefill_rows || []).length} rows`);
      } else {
        markShellTile(i, 'error', 'No text');
      }
    } catch (exc) {
      const msg = String((exc && exc.message) || exc);
      markShellTile(i, 'error', /tesseract/i.test(msg) ? 'No OCR engine' : 'Failed');
      AppLog.error('OCR import failed: ' + exc);
    }
  }

  closeScanShell();
  if (!photos.length) {
    showToast('No text found in those files — try clearer photos or a CSV');
    return;
  }
  const groups = photos.map((_, k) => [k]);
  routeScanResult(photos, groups, template);
}

/** @deprecated single-file shim kept for callers; routes through beginScanImport. */
export async function openOcrImport(mountElement, file, template = 'generic') {
  return beginScanImport(mountElement, [file], template);
}
```

Refactor `scanReceived` so the multi/single split delegates to `routeScanResult`. Replace its `payload.photos`/`payload.pages` branches with:

```js
  if (payload.photos && payload.photos.length) {
    const photos = payload.photos.map((p, i) => ({
      index: i, filename: p.filename || `scan-${i + 1}.jpg`,
      image_b64: p.image_b64 || '', pages: p.pages || [],
      prefill_rows: p.prefill_rows || [],
    }));
    routeScanResult(photos, payload.groups, payload.template || 'generic');
    return;
  }
  if (payload.pages && payload.pages.length) {
    routeScanResult(
      [{ index: 0, filename: (payload.filename || 'scan.jpg'),
         image_b64: scanSourceB64(payload), pages: payload.pages,
         prefill_rows: payload.prefill_rows || payload.line_items || [] }],
      [[0]], payload.template || 'generic', scanSourceFile(payload));
    return;
  }
```

Where `scanSourceB64` extracts the raw bytes the phone sent (reuse the same source `scanSourceFile` already derives). If `scanSourceFile(payload)` returns `{ name, bytes }`, add a tiny local:

```js
function scanSourceB64(payload) { const s = scanSourceFile(payload); return s ? s.bytes : ''; }
```

Keep the legacy flat-item fallback (`payload.line_items` with no `pages`) below, unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run tests/js/route-scan-result.test.mjs`
Expected: PASS (2 tests).

- [ ] **Step 5: JS gate**

Run: `npx eslint js/ && npx tsc --noEmit && npx vitest run`
Expected: clean (no eslint/type errors; all unit tests pass).

- [ ] **Step 6: Commit**

```bash
git add js/import/mfg-direct/mfg-direct-panel.js tests/js/route-scan-result.test.mjs
git commit -m "feat(scan): unified beginScanImport + shared routeScanResult"
```

---

## Task 7: Frontend — highlight by detecting model (row bbox or token fuzzy-match)

**Files:**
- Create: `js/import/mfg-direct/ocr-overlay/ocr-overlay-highlight.js`
- Modify: `js/import/mfg-direct/ocr-overlay/ocr-overlay-renderer.js` (render row bbox highlight + backend tag)
- Modify: `js/import/mfg-direct/ocr-overlay/ocr-overlay-panel.js` (compute highlight on cell focus)
- Test: `tests/js/ocr-overlay-highlight.test.mjs` (create)

**Interfaces:**
- Produces:
  - `rowHighlightBoxes(row, page) -> [{x,y,w,h}]` — if `row.bbox` is a 4-number array, returns `[{x,y,w,h}]` from it; else fuzzy-matches the row's text fields against `page.words` and returns the matched word boxes (possibly empty).
  - `backendLabel(backend) -> string` — `vlm`→"VLM", `grid`→"OCR grid", `flat`→"OCR", else "".

- [ ] **Step 1: Write the failing test**

```js
// tests/js/ocr-overlay-highlight.test.mjs
import { describe, it, expect } from 'vitest';
import { rowHighlightBoxes, backendLabel } from '../../js/import/mfg-direct/ocr-overlay/ocr-overlay-highlight.js';

const page = { width: 100, height: 100, words: [
  { text: 'C12345', x: 10, y: 10, w: 30, h: 8 },
  { text: '100', x: 50, y: 10, w: 12, h: 8 },
  { text: 'NOISE', x: 0, y: 90, w: 20, h: 8 },
] };

describe('rowHighlightBoxes', () => {
  it('uses the row bbox when present (VLM)', () => {
    const row = { _backend: 'vlm', bbox: [5, 6, 40, 12], distributor_pn: 'C12345' };
    expect(rowHighlightBoxes(row, page)).toEqual([{ x: 5, y: 6, w: 40, h: 12 }]);
  });

  it('falls back to fuzzy token match when bbox is null', () => {
    const row = { _backend: 'flat', bbox: null, distributor_pn: 'C12345', quantity: 100 };
    const boxes = rowHighlightBoxes(row, page);
    expect(boxes).toContainEqual({ x: 10, y: 10, w: 30, h: 8 });
    expect(boxes).toContainEqual({ x: 50, y: 10, w: 12, h: 8 });
    expect(boxes).not.toContainEqual({ x: 0, y: 90, w: 20, h: 8 });
  });

  it('returns empty array when nothing matches', () => {
    const row = { _backend: 'flat', bbox: null, mpn: 'ZZZ' };
    expect(rowHighlightBoxes(row, page)).toEqual([]);
  });
});

describe('backendLabel', () => {
  it('maps known backends', () => {
    expect(backendLabel('vlm')).toBe('VLM');
    expect(backendLabel('grid')).toBe('OCR grid');
    expect(backendLabel('flat')).toBe('OCR');
    expect(backendLabel('???')).toBe('');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run tests/js/ocr-overlay-highlight.test.mjs`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the highlight module**

```js
// js/import/mfg-direct/ocr-overlay/ocr-overlay-highlight.js
/* Highlight boxes for a prefill row, attributed to the model that produced it.
 * VLM rows carry their own pixel bbox; grid/flat rows (Tesseract) are matched to
 * the Tesseract word tokens whose text appears in the row's fields. */

const _LABELS = { vlm: 'VLM', grid: 'OCR grid', flat: 'OCR' };

export function backendLabel(backend) { return _LABELS[backend] || ''; }

function _norm(s) { return String(s == null ? '' : s).toLowerCase().replace(/[^a-z0-9]/g, ''); }

export function rowHighlightBoxes(row, page) {
  if (!row || !page) return [];
  const bb = row.bbox;
  if (Array.isArray(bb) && bb.length === 4 && bb.every(n => typeof n === 'number')) {
    return [{ x: bb[0], y: bb[1], w: bb[2], h: bb[3] }];
  }
  // Fuzzy match: collect normalized text from the row's value fields, then keep
  // any word token whose normalized text is a substring (length ≥ 2) of a field.
  const fieldVals = ['distributor_pn', 'mpn', 'manufacturer', 'description',
    'package', 'quantity', 'unit_price']
    .map(k => _norm(row[k])).filter(v => v.length >= 2);
  if (!fieldVals.length) return [];
  const boxes = [];
  for (const w of (page.words || [])) {
    const wt = _norm(w.text);
    if (wt.length < 2) continue;
    if (fieldVals.some(v => v.includes(wt) || wt.includes(v))) {
      boxes.push({ x: w.x, y: w.y, w: w.w, h: w.h });
    }
  }
  return boxes;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run tests/js/ocr-overlay-highlight.test.mjs`
Expected: PASS (5 assertions across 4 tests).

- [ ] **Step 5: Wire highlight into the overlay render + panel**

In `ocr-overlay-state.js`, preserve `_backend`/`bbox` on rows (already preserved via `{ ...r }` spread in `createState`) and add a focused-row marker. Add to `createState` return: `focusRow: null,`. Add a setter:

```js
export function setFocusRow(state, row) { return { ...state, focusRow: row }; }
```

In `ocr-overlay-renderer.js`, import the highlight helper and render row-highlight boxes + a backend tag. At top:

```js
import { rowHighlightBoxes, backendLabel } from './ocr-overlay-highlight.js';
```

In `renderScan`, after the token buttons, append highlight rectangles for the focused row:

```js
  const focus = state && state.focusRow != null ? state.rows[state.focusRow] : null;
  const hi = focus ? rowHighlightBoxes(focus, page).map(b => {
    const l = (b.x / page.width) * 100, t = (b.y / page.height) * 100;
    const w = (b.w / page.width) * 100, h = (b.h / page.height) * 100;
    const cls = (focus._backend === 'vlm') ? 'ocr-hi ocr-hi-vlm' : 'ocr-hi ocr-hi-ocr';
    return `<div class="${cls}" style="left:${l}%;top:${t}%;width:${w}%;height:${h}%"></div>`;
  }).join('') : '';
```

and include `${hi}` inside the `.ocr-img-wrap` (after `${tokens}`). Update `renderScan(page, pageIdx, state, selected)` call sites already pass `state`.

In `renderGrid`, add a backend tag to the row delete cell so the user sees what read each row:

```js
    const tag = backendLabel(row._backend);
    const del = `<td class="ocr-row-delete" data-row="${ri}" title="Delete row">×</td>`
      + (tag ? `<td class="ocr-row-backend" title="Detected by ${tag}">${tag}</td>` : `<td class="ocr-row-backend"></td>`);
```

and add a matching empty header cell before the field headers:

```js
  const head = '<th class="ocr-row-delete"></th><th class="ocr-row-backend"></th>' + fields.map(...).join('');
```

In `ocr-overlay-panel.js`, import `setFocusRow` and set the focused row when a cell is clicked/focused, so the highlight follows the user. In `bindEvents`, inside the `.ocr-cell` loop add to the `onclick`:

```js
    td.onclick = (e) => {
      if (e.detail === 2) return;
      state = setFocusRow(applyPending(selectCell(state, { row, field })), row);
      rerender();
    };
```

Add CSS to `css/styles.css`:

```css
.ocr-hi { position: absolute; pointer-events: none; border-radius: 2px; }
.ocr-hi-vlm { outline: 2px solid #7b1fa2; background: rgba(123,31,162,0.15); }
.ocr-hi-ocr { outline: 2px solid #1565c0; background: rgba(21,101,192,0.12); }
.ocr-row-backend { font-size: 10px; color: var(--muted, #888); white-space: nowrap; padding: 0 4px; }
```

- [ ] **Step 6: JS gate**

Run: `npx eslint js/ && npx tsc --noEmit && npx vitest run`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add js/import/mfg-direct/ocr-overlay/ tests/js/ocr-overlay-highlight.test.mjs css/styles.css
git commit -m "feat(ocr): highlight rows by the model that detected them"
```

---

## Task 8: Frontend — switch template after upload (re-parse + vendor prefill)

**Files:**
- Create: `js/import/mfg-direct/template-switch.js`
- Modify: `js/import/mfg-direct/ocr-overlay/ocr-overlay-renderer.js` (template `<select>` + "Re-scan" button in header)
- Modify: `js/import/mfg-direct/ocr-overlay/ocr-overlay-panel.js` (wire the select, the re-scan button, vendor prefill)
- Modify: `js/import/mfg-direct/ocr-overlay/ocr-overlay-state.js` (add `setTemplateAndReparse`)
- Test: `tests/js/template-switch.test.mjs` (create)

**Interfaces:**
- Consumes: `parse_with_template` semantics — but client-side we re-run only the column routing + cached text; the heavy re-parse is the opt-in re-scan via `apiMfgDirect.ocrOverlayB64`.
- Produces:
  - `templateVendorName(template) -> string | null` — `lcsc`→"LCSC", `digikey`→"DigiKey", `mouser`→"Mouser", `pololu`→"Pololu", else `null`.
  - `reparseRowsForTemplate(rows, template) -> rows` — re-routes `distributor`/`distributor_pn` columns for the new template without re-OCR (keeps cached text values).
  - `setTemplateAndReparse(state, template)` in state module — returns new state with `template` set and rows re-routed.

- [ ] **Step 1: Write the failing test**

```js
// tests/js/template-switch.test.mjs
import { describe, it, expect } from 'vitest';
import { templateVendorName, reparseRowsForTemplate } from '../../js/import/mfg-direct/template-switch.js';

describe('templateVendorName', () => {
  it('maps distributor templates to vendor names', () => {
    expect(templateVendorName('lcsc')).toBe('LCSC');
    expect(templateVendorName('digikey')).toBe('DigiKey');
    expect(templateVendorName('generic')).toBeNull();
  });
});

describe('reparseRowsForTemplate', () => {
  it('drops distributor_pn into distributor column for distributor templates', () => {
    const rows = [{ mpn: 'X', distributor: 'generic', distributor_pn: '' }];
    const out = reparseRowsForTemplate(rows, 'lcsc');
    expect(out[0].distributor).toBe('lcsc');
  });

  it('clears distributor for generic', () => {
    const rows = [{ mpn: 'X', distributor: 'lcsc', distributor_pn: 'C1' }];
    const out = reparseRowsForTemplate(rows, 'generic');
    expect(out[0].distributor).toBe('generic');
    expect(out[0].distributor_pn).toBe('');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run tests/js/template-switch.test.mjs`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `template-switch.js`**

```js
// js/import/mfg-direct/template-switch.js
/* Switching the template after upload: re-route distributor columns over the
 * already-extracted rows (no re-OCR) and resolve a matching vendor name. */

const _VENDOR = { lcsc: 'LCSC', digikey: 'DigiKey', mouser: 'Mouser', pololu: 'Pololu' };
const _DIST = new Set(['lcsc', 'digikey', 'mouser', 'pololu']);

export function templateVendorName(template) {
  return _VENDOR[(template || '').toLowerCase()] || null;
}

export function reparseRowsForTemplate(rows, template) {
  const t = (template || 'generic').toLowerCase();
  const isDist = _DIST.has(t);
  return (rows || []).map(r => {
    const next = { ...r };
    next.distributor = isDist ? t : 'generic';
    if (!isDist) next.distributor_pn = '';
    return next;
  });
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run tests/js/template-switch.test.mjs`
Expected: PASS (4 assertions).

- [ ] **Step 5: Add state setter + UI wiring**

In `ocr-overlay-state.js`:

```js
import { reparseRowsForTemplate } from '../template-switch.js';

export function setTemplateAndReparse(state, template) {
  return { ...state, template, rows: reparseRowsForTemplate(state.rows, template) };
}
```

In `ocr-overlay-renderer.js` `renderHeader`, replace the static `template: ${escHtml(state.template)}` text with a `<select>` and a re-scan button:

```js
  const tmplOpts = ['generic', 'lcsc', 'digikey', 'mouser', 'pololu']
    .map(k => `<option value="${k}"${state.template === k ? ' selected' : ''}>${k}</option>`).join('');
  const tmplCtl = `<label class="ocr-tmpl">Template:
    <select id="ocr-template-select">${tmplOpts}</select></label>
    <button id="ocr-rescan" type="button" class="btn-sm" title="Re-OCR with this template">↻ Re-scan</button>`;
```

and use `${tmplCtl}` in the returned header string (in place of the old template text).

In `ocr-overlay-panel.js`, import the new helpers:

```js
import { setTemplateAndReparse } from './ocr-overlay-state.js';
import { templateVendorName } from '../template-switch.js';
```

Add a module marker for auto-filled vendor and a prefill helper:

```js
let autoVendorName = '';

function maybePrefillVendor(template) {
  const name = templateVendorName(template);
  if (!name) return;  // generic: leave vendor untouched
  const cur = (vendor.name || '').trim();
  // Only fill when empty or still showing a previous auto-fill (don't clobber a
  // hand-typed vendor).
  if (cur && cur.toLowerCase() !== autoVendorName.toLowerCase()) return;
  autoVendorName = name;
  vendorPicker.onVendorNameBlur(name);  // find-or-create + select + onChange→rerender
}
```

Wire the select + re-scan button in `bindEvents`:

```js
  const tmplSel = root.querySelector('#ocr-template-select');
  if (tmplSel) tmplSel.onchange = () => {
    state = setTemplateAndReparse(state, tmplSel.value);
    maybePrefillVendor(tmplSel.value);
    rerender();
  };
  const rescan = root.querySelector('#ocr-rescan');
  if (rescan) rescan.onclick = onRescanClick;
```

Add the opt-in re-scan (fresh backend pass with the current template). It needs the source bytes; capture them when the overlay opens. Extend `openOverlay` signature to accept `sourceB64`/`sourceName` (passed by `routeScanResult`/queue) and store them:

```js
let sourceB64 = '';
let sourceName = '';
// in openOverlay(...): sourceB64 = opts.sourceB64 || ''; sourceName = opts.sourceName || '';

async function onRescanClick() {
  if (!sourceB64) { showToast('No source image to re-scan'); return; }
  showToast('Re-scanning with ' + state.template + '…');
  try {
    const payload = await apiMfgDirect.ocrOverlayB64(sourceB64, sourceName || 'scan.jpg', state.template);
    if (payload && payload.pages && payload.pages.length) {
      const keepVendor = { ...vendor };
      openOverlay(payload, { onConfirm: onConfirmCb, initialVendor: keepVendor,
        sourceB64, sourceName });
      return;
    }
    showToast('Re-scan found no text');
  } catch (exc) {
    AppLog.error('Re-scan failed: ' + exc);
    showToast('Re-scan failed — see log');
  }
}
```

Import `apiMfgDirect` in the panel (add to the existing `../../api.js` import). Update `routeScanResult` (Task 6) and `_openNextInQueue` to pass `sourceB64`/`sourceName` into `openOverlay` (single-photo: `only.image_b64`/`only.filename`; queue: `gp.image_b64`/`gp.filename`).

Reset `autoVendorName`/`sourceB64`/`sourceName` in `closeOverlay`.

- [ ] **Step 6: JS gate**

Run: `npx eslint js/ && npx tsc --noEmit && npx vitest run`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add js/import/mfg-direct/ tests/js/template-switch.test.mjs
git commit -m "feat(ocr): switch template after upload + auto-prefill vendor + re-scan"
```

---

## Task 9: E2E — responsive shell, multi-image grouping, template switch

**Files:**
- Create: `tests/e2e/scan-flow-responsive.spec.mjs`
- Reference: existing E2E harness/fixtures under `tests/e2e/` for how the import panel is mounted and how the OCR API is stubbed.

**Interfaces:**
- Consumes: `beginScanImport` path via the real `#import-ocr-zone`; the OCR backend stubbed/served as the existing E2E setup does for `ocr_overlay_b64`.

- [ ] **Step 1: Inspect the existing E2E setup**

Run: `ls tests/e2e && grep -rl "import-ocr-zone\|ocr_overlay_b64\|openOverlay" tests/e2e`
Read the closest existing import/OCR E2E spec to copy its mounting + API-stub pattern (do not invent a new harness).

- [ ] **Step 2: Write the E2E spec (realistic interactions only)**

```js
// tests/e2e/scan-flow-responsive.spec.mjs
import { test, expect } from '@playwright/test';
// Follow the existing harness import in tests/e2e for app bootstrap + OCR stub.

test('dropping one image shows the Reading shell immediately, then the overlay', async ({ page }) => {
  // ... bootstrap app + stub ocr_overlay_b64 to resolve after a short delay so
  // the shell is observable before the overlay opens ...
  // Use setInputFiles on #import-ocr-input (realistic browse path).
  await page.setInputFiles('#import-ocr-input', 'tests/fixtures/scan-single.png');
  await expect(page.locator('#scan-shell-overlay')).toBeVisible();        // instant
  await expect(page.locator('#ocr-overlay')).toBeVisible();               // after OCR
  await expect(page.locator('#scan-shell-overlay')).toHaveCount(0);
});

test('selecting three images routes to the grouping editor', async ({ page }) => {
  await page.setInputFiles('#import-ocr-input',
    ['tests/fixtures/scan-a.png', 'tests/fixtures/scan-b.png', 'tests/fixtures/scan-c.png']);
  await expect(page.locator('#scan-grouping-overlay')).toBeVisible();
  await expect(page.locator('.scan-thumb')).toHaveCount(3);
});

test('switching template in the overlay re-routes the column and fills the vendor', async ({ page }) => {
  await page.setInputFiles('#import-ocr-input', 'tests/fixtures/scan-single.png');
  await expect(page.locator('#ocr-overlay')).toBeVisible();
  await page.selectOption('#ocr-template-select', 'lcsc');
  await expect(page.locator('th', { hasText: 'LCSC#' })).toBeVisible();   // dist column
  await expect(page.locator('#ocr-vendor-name-input')).toHaveValue(/LCSC/i);
});
```

(Add small PNG fixtures under `tests/fixtures/` if the harness needs real files; reuse any existing scan fixture if present.)

- [ ] **Step 3: Run the E2E spec**

Run: `npx playwright test scan-flow-responsive`
Expected: PASS (3 tests).

- [ ] **Step 4: Run the clipping guards (responsive-layout safety)**

Run: `npx playwright test sticky-buttons resize-visibility`
Expected: PASS — if they fail, fix the CSS (never weaken the tests).

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/scan-flow-responsive.spec.mjs tests/fixtures/
git commit -m "test(e2e): responsive shell, multi-image grouping, template switch"
```

---

## Task 10: Full verification + branch finish

**Files:** none (verification only).

- [ ] **Step 1: Regenerate fixtures (backend row shape changed)**

Run: `python scripts/generate-test-fixtures.py`
Expected: completes; commit any changed `tests/fixtures/generated/*.json`.

- [ ] **Step 2: Python gate**

Run: `ruff check . && pytest tests/python/ -v`
Expected: all pass.

- [ ] **Step 3: JS gate**

Run: `npx eslint js/ && npx tsc --noEmit && npx vitest run`
Expected: all pass.

- [ ] **Step 4: E2E (full import + clipping)**

Run: `npx playwright test scan-flow-responsive sticky-buttons resize-visibility`
Expected: all pass.

- [ ] **Step 5: Commit any fixture deltas + open PR**

```bash
git add -A && git commit -m "chore: regenerate fixtures for scan-flow changes" || true
bash scripts/push-pr.sh --title "feat(scan): responsive unified scan/OCR flow"
```
Then monitor CI (`gh pr checks <number>`) and fix failures until green.

---

## Self-Review

**Spec coverage:**
- Immediate shell (spec §1) → Task 5 + Task 6 (`openScanShell` called first in `beginScanImport`); E2E asserts it (Task 9).
- Unified entry, drag/browse/phone converge (spec §1) → Task 6 (`beginScanImport`/`routeScanResult`; `scanReceived` refactor); wiring Task 4.
- Multi-image drag/browse (spec §1/§2) → Task 4 (`multiple` + array) + Task 6 (per-file loop → grouping); E2E Task 9.
- VLM bbox + `_backend` (spec §3/§5) → Task 1 (VLM), Task 3 (grid/flat tag + page size); highlight Task 7.
- Highlight by detecting model + Tesseract fallback (spec §3) → Task 7.
- Template switch after upload, instant re-route, vendor prefill, opt-in re-scan (spec §4) → Task 8; E2E Task 9.
- VLM template hint (spec §4) → Task 2.
- Testing requirements (spec Testing) → Tasks 1-9 unit/E2E; Task 10 gates + fixture regen + clipping guards.

**Placeholder scan:** Task 9 leaves the E2E bootstrap/stub to the existing harness (Step 1 inspects it) — this is deliberate (don't invent a parallel harness), not a content gap; the assertions and interactions are concrete.

**Type consistency:** `_backend`/`bbox` field names consistent across Tasks 1/3/7. `beginScanImport(mount, files, template)` / `routeScanResult(photos, groups, template, sourceHint)` consistent between Tasks 4/6/8. `photos[i]` shape `{index, filename, image_b64, pages, prefill_rows}` matches `scan-grouping.js` `_photos` shape and `buildGroupPayloads`. `openOverlay` extended with `sourceB64`/`sourceName` consistently in Tasks 6/8. `templateVendorName`/`reparseRowsForTemplate`/`rowHighlightBoxes`/`backendLabel` signatures match their tests.
