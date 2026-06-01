# OCR-overlay PO import modal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the mfg-direct line-item editor (for image/PDF inputs and phone scans) with an Apple Live-Text-style modal: the scan with clickable OCR word/line overlays on the left, the auto-filled distributor-template grid on the right, with bidirectional click-to-fill, drag-to-combine, double-click-to-edit, multi-page navigation, and low-confidence flagging.

**Architecture:** A new backend call rasterizes the input (PyMuPDF for PDFs, passthrough for images) to per-page PNGs, runs `pytesseract.image_to_data` for word/line tokens + bounding boxes + confidence, and runs the existing `distributor_profiles` heuristic to pre-fill the grid — returning everything in one payload. New vanilla-ES-module frontend (`js/import/mfg-direct/ocr-overlay/`: pure `state`, `renderer`, `panel`) builds the modal, positions tokens as % of page size, manages selection/assignment, and on confirm hands rows + vendor to the existing `create_purchase_order_with_items` path. Styling reuses existing CSS tokens (`css/variables.css`, `css/tokens.css`).

**Tech Stack:** Python (pywebview backend), `pytesseract`, `Pillow`, **PyMuPDF (`fitz`)** (new), vanilla JS ES modules (no build step), vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-05-31-ocr-overlay-po-import-design.md`

---

## File Structure

| File | Responsibility | New/Modify |
|------|----------------|-----------|
| `requirements.txt` | add `PyMuPDF>=1.24` | Modify |
| `pdf_raster.py` | Rasterize PDF bytes → list of (PNG bytes, w, h); images pass through | Create |
| `ocr_layout.py` | `image_to_data` → words/lines (+bbox, conf); `extract_pages()` orchestrates raster+OCR+prefill | Create |
| `inventory_api.py` | `ocr_overlay_b64(file_b64, file_name, template)` API method | Modify |
| `pnp_server.py` | phone `/api/scan/upload` returns the overlay payload (push `_scanReceived` with pages) | Modify |
| `js/api.js` | `apiMfgDirect.ocrOverlayB64` wrapper | Modify |
| `js/import/mfg-direct/ocr-overlay/ocr-overlay-state.js` | Pure selection/assignment/drag/page state + transforms | Create |
| `js/import/mfg-direct/ocr-overlay/ocr-overlay-renderer.js` | Modal DOM: split panes, positioned tokens, grid, page nav, vendor picker mount | Create |
| `js/import/mfg-direct/ocr-overlay/ocr-overlay-panel.js` | Event wiring, API call, confirm→importPO bridge | Create |
| `js/import/mfg-direct/mfg-direct-panel.js` | Route image/PDF drops + `_scanReceived` to the overlay modal | Modify |
| `css/tokens.css` | new `--ocr-*` layout dims (token, not raw px) | Modify |
| `css/components/ocr-overlay.css` | modal styling using existing color/token vars | Create |
| `index.html` | `<link>` the new css | Modify |
| `tests/python/test_pdf_raster.py` | raster tests | Create |
| `tests/python/test_ocr_layout.py` | OCR layout + prefill tests | Create |
| `tests/python/test_ocr_overlay_api.py` | API shape test | Create |
| `tests/js/ocr-overlay-state.test.js` | pure-state unit tests | Create |
| `tests/js/e2e/ocr-overlay.spec.mjs` | realistic E2E | Create |

**Data shapes (used across tasks — keep names exact):**

```python
# ocr_layout token (pixel coords relative to that page's width/height)
Word = {"text": str, "x": int, "y": int, "w": int, "h": int,
        "conf": float, "line_id": int}     # line_id groups words on one OCR line
Line = {"text": str, "x": int, "y": int, "w": int, "h": int, "conf": float}
Page = {"image_b64": str, "width": int, "height": int,
        "words": list[Word], "lines": list[Line]}
# ocr_overlay_b64 return:
Overlay = {"pages": list[Page], "prefill_rows": list[dict], "template": str}
# prefill_rows: same keys the existing heuristic emits
#   (mpn, manufacturer, package, quantity, unit_price, distributor, distributor_pn)
#   plus "_low_conf": list[str] naming fields whose source word conf < 60.
```

```js
// ocr-overlay-state shapes
// token id = `${pageIdx}:${kind}:${index}` where kind ∈ {"w","l"}
// pending = { kind: "source"|"target"|null, tokenIds: string[], cell: {row,field}|null }
```

---

## Task 1: Add PyMuPDF + PDF rasterization

**Files:**
- Modify: `requirements.txt`
- Create: `pdf_raster.py`
- Test: `tests/python/test_pdf_raster.py`

- [ ] **Step 1: Add dependency**

Edit `requirements.txt`, append after `Pillow>=10`:
```
PyMuPDF>=1.24
```
Run: `pip install -r requirements.txt`

- [ ] **Step 2: Write the failing test**

Create `tests/python/test_pdf_raster.py`:
```python
import io
import fitz  # PyMuPDF
import pdf_raster


def _one_page_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    page.insert_text((20, 50), "HELLO PO")
    data = doc.tobytes()
    doc.close()
    return data


def test_rasterize_pdf_returns_one_page_with_dims():
    pages = pdf_raster.rasterize(_one_page_pdf(), ".pdf")
    assert len(pages) == 1
    png, w, h = pages[0]
    assert png[:8] == b"\x89PNG\r\n\x1a\n"     # PNG magic
    assert w > 300 and h > 200                  # scaled up at >72 DPI


def test_rasterize_multipage_pdf():
    doc = fitz.open()
    doc.new_page(width=300, height=200)
    doc.new_page(width=300, height=200)
    data = doc.tobytes(); doc.close()
    pages = pdf_raster.rasterize(data, ".pdf")
    assert len(pages) == 2


def test_image_passthrough_returns_single_page():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buf, format="PNG")
    pages = pdf_raster.rasterize(buf.getvalue(), ".png")
    assert len(pages) == 1
    png, w, h = pages[0]
    assert (w, h) == (40, 30)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/python/test_pdf_raster.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pdf_raster'`

- [ ] **Step 4: Implement `pdf_raster.py`**

```python
"""Rasterize source documents to per-page PNG bytes for the OCR-overlay modal.

PDFs are rendered with PyMuPDF (no system Poppler dependency). Image files are
returned as a single page unchanged (re-encoded to PNG for a uniform contract).
"""

from __future__ import annotations

import io

_PDF_DPI = 180  # render scale; 72 = native. Higher = crisper OCR, bigger payload.


def rasterize(data: bytes, ext: str) -> list[tuple[bytes, int, int]]:
    """Return [(png_bytes, width, height), ...], one entry per page.

    ext: lowercased file extension including dot (".pdf", ".png", ".jpg", ...).
    """
    ext = (ext or "").lower()
    if ext == ".pdf":
        return _rasterize_pdf(data)
    return [_image_to_png_page(data)]


def _rasterize_pdf(data: bytes) -> list[tuple[bytes, int, int]]:
    import fitz

    zoom = _PDF_DPI / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    pages: list[tuple[bytes, int, int]] = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        for page in doc:
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            pages.append((pix.tobytes("png"), pix.width, pix.height))
    if not pages:
        raise ValueError("PDF contained no pages")
    return pages


def _image_to_png_page(data: bytes) -> tuple[bytes, int, int]:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue(), im.width, im.height
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/python/test_pdf_raster.py -q`
Expected: PASS (3 passed)
Run: `ruff check .` → All checks passed

- [ ] **Step 6: Commit**

```bash
git add requirements.txt pdf_raster.py tests/python/test_pdf_raster.py
git commit -m "feat(import): PDF/image rasterization for OCR overlay (PyMuPDF)"
```

---

## Task 2: OCR layout extraction (words/lines + confidence)

**Files:**
- Create: `ocr_layout.py`
- Test: `tests/python/test_ocr_layout.py`

- [ ] **Step 1: Write the failing test**

Create `tests/python/test_ocr_layout.py` (renders a high-contrast image like the existing `test_mfg_direct_import.py::TestParseImage` pattern — uses Pillow + tesseract binary; do NOT add pytest.skip, this mirrors the existing guarded OCR test):
```python
import io
import shutil
import pytest
from PIL import Image, ImageDraw, ImageFont

import ocr_layout

pytestmark = pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="tesseract binary not installed"
)


def _text_png(lines: list[str]) -> bytes:
    img = Image.new("RGB", (640, 60 * len(lines) + 40), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 32)
    except OSError:
        font = ImageFont.load_default()
    for i, ln in enumerate(lines):
        d.text((20, 20 + i * 60), ln, fill="black", font=font)
    buf = io.BytesIO(); img.save(buf, format="PNG")
    return buf.getvalue()


def test_words_have_text_bbox_and_conf():
    png = _text_png(["C12624 4000"])
    page = ocr_layout.extract_page(png)
    texts = [w["text"] for w in page["words"]]
    assert any("C12624" in t for t in texts)
    for w in page["words"]:
        for k in ("text", "x", "y", "w", "h", "conf", "line_id"):
            assert k in w
        assert w["w"] > 0 and w["h"] > 0


def test_lines_group_words_on_same_row():
    png = _text_png(["KT-0603G Emerald Green LED"])
    page = ocr_layout.extract_page(png)
    assert page["lines"], "expected at least one line"
    # the whole row collapses into one line token
    assert any("Emerald" in ln["text"] and "KT-0603G" in ln["text"]
               for ln in page["lines"])


def test_dimensions_returned():
    png = _text_png(["X"])
    page = ocr_layout.extract_page(png)
    assert page["width"] == 640 and page["height"] > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/python/test_ocr_layout.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ocr_layout'`

- [ ] **Step 3: Implement `ocr_layout.py`**

```python
"""Word/line-level OCR layout extraction for the OCR-overlay modal.

Uses pytesseract.image_to_data to get per-word bounding boxes and confidence,
then groups words sharing (block, par, line) into line tokens.
"""

from __future__ import annotations

import base64
import io
from typing import Any

LOW_CONF = 60.0  # words below this are flagged for review in prefill


def extract_page(png_bytes: bytes) -> dict[str, Any]:
    """Return {image_b64, width, height, words, lines} for one page image."""
    import pytesseract
    from PIL import Image

    with Image.open(io.BytesIO(png_bytes)) as im:
        im = im.convert("RGB")
        width, height = im.width, im.height
        data = pytesseract.image_to_data(im, output_type=pytesseract.Output.DICT)

    words: list[dict[str, Any]] = []
    groups: dict[tuple, list[int]] = {}
    n = len(data["text"])
    for i in range(n):
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (ValueError, TypeError):
            conf = -1.0
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        line_id = _line_index(groups, key)
        words.append({
            "text": text,
            "x": int(data["left"][i]), "y": int(data["top"][i]),
            "w": int(data["width"][i]), "h": int(data["height"][i]),
            "conf": conf, "line_id": line_id,
        })

    lines = _group_lines(words)
    return {
        "image_b64": base64.b64encode(png_bytes).decode("ascii"),
        "width": width, "height": height,
        "words": words, "lines": lines,
    }


def _line_index(groups: dict[tuple, list[int]], key: tuple) -> int:
    if key not in groups:
        groups[key] = [len(groups)]
    return groups[key][0]


def _group_lines(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_line: dict[int, list[dict[str, Any]]] = {}
    for w in words:
        by_line.setdefault(w["line_id"], []).append(w)
    lines = []
    for line_id, ws in sorted(by_line.items()):
        ws = sorted(ws, key=lambda w: w["x"])
        x0 = min(w["x"] for w in ws)
        y0 = min(w["y"] for w in ws)
        x1 = max(w["x"] + w["w"] for w in ws)
        y1 = max(w["y"] + w["h"] for w in ws)
        confs = [w["conf"] for w in ws if w["conf"] >= 0]
        lines.append({
            "text": " ".join(w["text"] for w in ws),
            "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0,
            "conf": (sum(confs) / len(confs)) if confs else -1.0,
        })
    return lines
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/python/test_ocr_layout.py -q`
Expected: PASS (skips only if tesseract binary absent — same as existing OCR test)
Run: `ruff check .` → pass

- [ ] **Step 5: Commit**

```bash
git add ocr_layout.py tests/python/test_ocr_layout.py
git commit -m "feat(import): word/line OCR layout extraction with confidence"
```

---

## Task 3: `extract_pages` orchestration + prefill + API method + js wrapper

**Files:**
- Modify: `ocr_layout.py` (add `extract_pages`)
- Modify: `inventory_api.py` (add `ocr_overlay_b64`)
- Modify: `js/api.js`
- Test: `tests/python/test_ocr_overlay_api.py`

- [ ] **Step 1: Write the failing test**

Create `tests/python/test_ocr_overlay_api.py`:
```python
import io
import shutil
import pytest
from PIL import Image

from inventory_api import InventoryApi

pytestmark = pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="tesseract binary not installed"
)


def _png_b64(text_w=200, text_h=80) -> str:
    import base64
    buf = io.BytesIO()
    Image.new("RGB", (text_w, text_h), "white").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_ocr_overlay_returns_pages_and_prefill(tmp_path):
    api = InventoryApi(base_dir=str(tmp_path))   # match existing InventoryApi ctor
    out = api.ocr_overlay_b64(_png_b64(), "scan.png", "lcsc")
    assert out["template"] == "lcsc"
    assert isinstance(out["pages"], list) and len(out["pages"]) == 1
    page = out["pages"][0]
    for k in ("image_b64", "width", "height", "words", "lines"):
        assert k in page
    assert isinstance(out["prefill_rows"], list)
```

NOTE: confirm `InventoryApi`'s constructor signature in `inventory_api.py` and adjust the `InventoryApi(...)` call to match (it may be `InventoryApi(debug=...)` with `base_dir` derived). Use whatever the existing Python API tests use.

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/python/test_ocr_overlay_api.py -q`
Expected: FAIL — `AttributeError: 'InventoryApi' object has no attribute 'ocr_overlay_b64'`

- [ ] **Step 3: Add `extract_pages` to `ocr_layout.py`**

```python
def extract_pages(file_bytes: bytes, ext: str, template: str = "generic") -> dict[str, Any]:
    """Rasterize -> OCR each page -> heuristic prefill. Returns the Overlay dict."""
    import pdf_raster
    import mfg_direct_import

    pages = [extract_page(png) for (png, _w, _h) in pdf_raster.rasterize(file_bytes, ext)]

    # Prefill from the combined OCR text across pages, via the existing heuristic.
    full_text = "\n".join(
        ln["text"] for pg in pages for ln in pg["lines"]
    )
    prefill_rows = mfg_direct_import.parse_text_with_template(template, full_text)  # see note

    return {"pages": pages, "prefill_rows": prefill_rows, "template": template}
```

NOTE: `mfg_direct_import`/`distributor_profiles` already expose template parsing (used by `parse_source_file`). Reuse the existing public entry that turns OCR text into line-item dicts (e.g. `distributor_profiles.parse_with_template(template, text)` with generic fallback). Confirm the exact function name in `distributor_profiles.py` and call that; do not duplicate the heuristic. Add `_low_conf` later only if cheap — MVP may leave it empty.

- [ ] **Step 4: Add API method to `inventory_api.py`** (mirror `parse_source_file_b64`, lines 628-649)

```python
    def ocr_overlay_b64(
        self, file_b64: str, file_name: str, template: str = "generic",
    ) -> dict[str, Any]:
        """Decode base64, rasterize+OCR all pages, heuristic-prefill the grid.

        Returns {pages:[{image_b64,width,height,words,lines}], prefill_rows, template}.
        """
        import base64
        import ocr_layout

        ext = os.path.splitext(file_name)[1].lower()
        data = base64.b64decode(file_b64)
        return ocr_layout.extract_pages(data, ext, template)
```

- [ ] **Step 5: Add js wrapper to `js/api.js`** (in `apiMfgDirect`, after `parseFileB64`)

```js
  ocrOverlayB64: (b64, name, template = 'generic') =>
    api('ocr_overlay_b64', b64, name, template),
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/python/test_ocr_overlay_api.py tests/python/test_ocr_layout.py tests/python/test_pdf_raster.py -q`
Expected: PASS
Run: `ruff check . && npx eslint js/` → pass

- [ ] **Step 7: Commit**

```bash
git add ocr_layout.py inventory_api.py js/api.js tests/python/test_ocr_overlay_api.py
git commit -m "feat(import): ocr_overlay_b64 API — pages + heuristic prefill"
```

---

## Task 4: Frontend pure state module

**Files:**
- Create: `js/import/mfg-direct/ocr-overlay/ocr-overlay-state.js`
- Test: `tests/js/ocr-overlay-state.test.js`

The state module is pure (no DOM/api). It owns: the overlay payload, current page index, the grid rows, and the pending selection; and exposes transforms used by the panel.

- [ ] **Step 1: Write the failing test**

Create `tests/js/ocr-overlay-state.test.js`:
```js
import { describe, it, expect } from 'vitest';
import {
  createState, selectToken, selectCell, applyPending,
  combineTokens, setCellValue, tokenText,
} from '../../js/import/mfg-direct/ocr-overlay/ocr-overlay-state.js';

const payload = {
  template: 'lcsc',
  pages: [{
    image_b64: 'AAAA', width: 100, height: 50,
    words: [
      { text: 'C12624', x: 0, y: 0, w: 30, h: 8, conf: 95, line_id: 0 },
      { text: 'KT-0603G', x: 0, y: 10, w: 40, h: 8, conf: 90, line_id: 1 },
    ],
    lines: [{ text: 'C12624', x: 0, y: 0, w: 30, h: 8, conf: 95 }],
  }],
  prefill_rows: [{ distributor_pn: '', mpn: '', quantity: 0, unit_price: 0 }],
};

describe('ocr-overlay-state', () => {
  it('word-first then cell fills the cell', () => {
    let s = createState(payload);
    s = selectToken(s, '0:w:0');                 // click word C12624
    expect(s.pending.kind).toBe('source');
    s = selectCell(s, { row: 0, field: 'distributor_pn' });
    s = applyPending(s);
    expect(s.rows[0].distributor_pn).toBe('C12624');
    expect(s.pending.kind).toBe(null);           // cleared after apply
  });

  it('cell-first then word fills the cell (reverse direction)', () => {
    let s = createState(payload);
    s = selectCell(s, { row: 0, field: 'mpn' });
    expect(s.pending.kind).toBe('target');
    s = selectToken(s, '0:w:1');
    s = applyPending(s);
    expect(s.rows[0].mpn).toBe('KT-0603G');
  });

  it('combines multiple tokens in x/y reading order', () => {
    expect(combineTokens(payload.pages[0], ['0:w:1', '0:w:0']))
      .toBe('C12624 KT-0603G');
  });

  it('double-click edit sets a value directly', () => {
    let s = createState(payload);
    s = setCellValue(s, 0, 'quantity', '4000');
    expect(s.rows[0].quantity).toBe('4000');
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run tests/js/ocr-overlay-state.test.js`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `ocr-overlay-state.js`**

```js
/* ocr-overlay-state.js — pure state for the OCR overlay modal (no DOM, no api). */

export function createState(payload) {
  return {
    template: payload.template,
    pages: payload.pages || [],
    pageIdx: 0,
    rows: (payload.prefill_rows || []).map(r => ({ ...r })),
    lowConf: (payload.prefill_rows || []).map(r => new Set(r._low_conf || [])),
    pending: { kind: null, tokenIds: [], cell: null },
  };
}

function tokenFromId(pages, id) {
  const [p, kind, idx] = id.split(':');
  const page = pages[+p];
  const arr = kind === 'w' ? page.words : page.lines;
  return arr[+idx];
}

export function tokenText(pages, id) {
  const t = tokenFromId(pages, id);
  return t ? t.text : '';
}

/** Combine token ids into one string in reading order (top, then left). */
export function combineTokens(page, ids) {
  const toks = ids.map(id => {
    const [, kind, idx] = id.split(':');
    return (kind === 'w' ? page.words : page.lines)[+idx];
  });
  toks.sort((a, b) => (a.y - b.y) || (a.x - b.x));
  return toks.map(t => t.text).join(' ');
}

export function selectToken(state, tokenId) {
  if (state.pending.kind === 'target' && state.pending.cell) {
    return { ...state, pending: { ...state.pending, kind: 'source', tokenIds: [tokenId] } };
  }
  return { ...state, pending: { kind: 'source', tokenIds: [tokenId], cell: null } };
}

export function selectTokens(state, tokenIds) {
  return { ...state, pending: { ...state.pending, kind: state.pending.cell ? 'source' : 'source', tokenIds } };
}

export function selectCell(state, cell) {
  if (state.pending.kind === 'source' && state.pending.tokenIds.length) {
    return { ...state, pending: { ...state.pending, cell } };
  }
  return { ...state, pending: { kind: 'target', tokenIds: [], cell } };
}

/** Complete an assignment if both a token set and a target cell are pending. */
export function applyPending(state) {
  const { tokenIds, cell } = state.pending;
  if (!tokenIds.length || !cell) return state;
  const page = state.pages[state.pageIdx];
  const value = combineTokens(page, tokenIds);
  const rows = state.rows.map((r, i) =>
    i === cell.row ? { ...r, [cell.field]: value } : r);
  const lowConf = state.lowConf.map((s, i) => {
    if (i !== cell.row) return s;
    const next = new Set(s); next.delete(cell.field); return next;  // user-confirmed
  });
  return { ...state, rows, lowConf, pending: { kind: null, tokenIds: [], cell: null } };
}

export function setCellValue(state, row, field, value) {
  const rows = state.rows.map((r, i) => i === row ? { ...r, [field]: value } : r);
  return { ...state, rows };
}

export function setPage(state, pageIdx) {
  return { ...state, pageIdx, pending: { kind: null, tokenIds: [], cell: null } };
}

export function clearPending(state) {
  return { ...state, pending: { kind: null, tokenIds: [], cell: null } };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run tests/js/ocr-overlay-state.test.js`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add js/import/mfg-direct/ocr-overlay/ocr-overlay-state.js tests/js/ocr-overlay-state.test.js
git commit -m "feat(import): pure state for OCR overlay (bidirectional fill, combine, edit)"
```

---

## Task 5: Modal renderer + CSS (app tokens)

**Files:**
- Create: `js/import/mfg-direct/ocr-overlay/ocr-overlay-renderer.js`
- Modify: `css/tokens.css` (add `--ocr-*` dims)
- Create: `css/components/ocr-overlay.css`
- Modify: `index.html` (link the css)

Renderer is DOM-building only (no event wiring). It must reuse existing color tokens
(`var(--bg-surface)`, `var(--bg-raised)`, `var(--border-default)`, `var(--color-blue)`,
`var(--color-yellow)`, `var(--text-primary)`, etc.) and the `.modal-overlay`/`.modal` classes.

- [ ] **Step 1: Add layout tokens to `css/tokens.css`** (so the layout-token guard passes — no raw px)

Append a block:
```css
  /* ── OCR overlay modal ─────────────────────────────────────────────── */
  --ocr-modal-w:        min(1200px, 95vw);
  --ocr-pane-gap:       12px;
  --ocr-token-pad:      1px;
  --ocr-token-font:     11px;
```

- [ ] **Step 2: Create `css/components/ocr-overlay.css`**

Use existing vars. Key rules (token positions are set inline as % by the renderer, which the guard does not scan in JS strings, but keep static dims in tokens):
```css
.ocr-overlay-modal { width: var(--ocr-modal-w); max-height: 90vh; display: flex; flex-direction: column; }
.ocr-split { display: flex; gap: var(--ocr-pane-gap); flex: 1; min-height: 0; }
.ocr-scan-pane, .ocr-grid-pane { flex: 1; overflow: auto; background: var(--bg-surface);
  border: 1px solid var(--border-default); border-radius: 6px; }
.ocr-img-wrap { position: relative; }                 /* tokens positioned within */
.ocr-img-wrap img { display: block; width: 100%; }
.ocr-token { position: absolute; font-size: var(--ocr-token-font); padding: var(--ocr-token-pad);
  background: rgba(88,166,255,0.12); border: 1px solid transparent; cursor: pointer;
  color: transparent; overflow: hidden; }            /* transparent text: shows image beneath */
.ocr-token:hover { border-color: var(--color-blue); background: rgba(88,166,255,0.25); }
.ocr-token.selected { border-color: var(--color-blue); background: rgba(88,166,255,0.35); }
.ocr-cell { background: var(--bg-input, var(--bg-raised)); color: var(--text-primary);
  border: 1px solid var(--border-default); }
.ocr-cell.target { border-color: var(--color-blue); box-shadow: 0 0 0 1px var(--color-blue); }
.ocr-cell.low-conf { border-color: var(--color-yellow); background: rgba(210,153,34,0.12); }
.ocr-cell.blank { color: var(--text-muted); font-style: italic; }
```

- [ ] **Step 3: Link the CSS in `index.html`**

Find where `css/components/*.css` are linked (e.g. near `vendor.css`) and add:
```html
<link rel="stylesheet" href="css/components/ocr-overlay.css">
```

- [ ] **Step 4: Implement `ocr-overlay-renderer.js`**

Functions (DOM strings; positions as % of page width/height so they scale):
```js
/* ocr-overlay-renderer.js — builds the OCR overlay modal DOM. */
import { escHtml } from '../../../ui-helpers.js';

export function renderModal(state) {
  const page = state.pages[state.pageIdx];
  return `<div class="modal-overlay" id="ocr-overlay">
    <div class="modal ocr-overlay-modal">
      ${renderHeader(state)}
      <div class="ocr-split">
        <div class="ocr-scan-pane">${renderScan(page, state.pageIdx)}</div>
        <div class="ocr-grid-pane">${renderGrid(state)}</div>
      </div>
      ${renderFooter(state)}
    </div>
  </div>`;
}

function renderHeader(state) {
  const n = state.pages.length;
  const nav = n > 1
    ? `<button id="ocr-prev" ${state.pageIdx === 0 ? 'disabled' : ''}>‹</button>
       <span>Page ${state.pageIdx + 1} / ${n}</span>
       <button id="ocr-next" ${state.pageIdx === n - 1 ? 'disabled' : ''}>›</button>`
    : '';
  return `<div class="ocr-header">Review scan — template: ${escHtml(state.template)} ${nav}</div>`;
}

function renderScan(page, pageIdx) {
  const tok = (kind, arr) => arr.map((t, i) => {
    const l = (t.x / page.width) * 100, top = (t.y / page.height) * 100;
    const w = (t.w / page.width) * 100, h = (t.h / page.height) * 100;
    return `<button class="ocr-token" data-token="${pageIdx}:${kind}:${i}"
      style="left:${l}%;top:${top}%;width:${w}%;height:${h}%"
      title="${escHtml(t.text)}">${escHtml(t.text)}</button>`;
  }).join('');
  return `<div class="ocr-img-wrap">
    <img src="data:image/png;base64,${page.image_b64}" alt="scan">
    ${tok('w', page.words)}
  </div>`;
  // NOTE: render line tokens too, toggled via a "words/lines" switch (Task 6 wires the toggle).
}

function renderGrid(state) {
  const fields = gridFields(state.template);   // ordered template columns; see below
  const head = fields.map(f => `<th>${escHtml(f.label)}</th>`).join('');
  const body = state.rows.map((row, ri) => {
    const cells = fields.map(f => {
      const v = row[f.key] ?? '';
      const cls = ['ocr-cell'];
      if (state.pending.cell && state.pending.cell.row === ri && state.pending.cell.field === f.key) cls.push('target');
      if (state.lowConf[ri] && state.lowConf[ri].has(f.key)) cls.push('low-conf');
      if (v === '' || v === 0) cls.push('blank');
      return `<td class="${cls.join(' ')}" data-row="${ri}" data-field="${f.key}"
        tabindex="0">${escHtml(String(v || ''))}</td>`;
    }).join('');
    return `<tr>${cells}</tr>`;
  }).join('');
  return `<table class="ocr-grid"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderFooter(state) {
  // Reuse the existing mfg-direct vendor picker markup here (import its renderer
  // fragment) so the user sets a vendor before confirm.
  return `<div class="ocr-footer">
    <span id="ocr-vendor-mount"></span>
    <button id="ocr-cancel" class="btn btn-cancel">Cancel</button>
    <button id="ocr-confirm" class="btn btn-primary">Continue to import</button>
  </div>`;
}

// Template field columns. Mirror PO_TEMPLATES from js/import/import-logic.js, mapped to
// the line-item keys used by importPO (mpn, distributor_pn, manufacturer, description,
// package, quantity, unit_price). Implement gridFields(template) accordingly.
export function gridFields(template) {
  const distLabel = { lcsc: 'LCSC#', digikey: 'DigiKey#', mouser: 'Mouser#', pololu: 'Pololu#' }[template];
  const cols = [];
  if (distLabel) cols.push({ key: 'distributor_pn', label: distLabel });
  cols.push(
    { key: 'mpn', label: 'Mfr Part#' },
    { key: 'manufacturer', label: 'Mfr' },
    { key: 'description', label: 'Description' },
    { key: 'package', label: 'Pkg' },
    { key: 'quantity', label: 'Qty' },
    { key: 'unit_price', label: '$/ea' },
  );
  return cols;
}
```

- [ ] **Step 5: Verify lint/types**

Run: `npx eslint js/ && npx tsc --noEmit` → pass

- [ ] **Step 6: Commit**

```bash
git add js/import/mfg-direct/ocr-overlay/ocr-overlay-renderer.js css/tokens.css css/components/ocr-overlay.css index.html
git commit -m "feat(import): OCR overlay modal renderer + themed CSS"
```

---

## Task 6: Panel (events) + integration into mfg-direct

**Files:**
- Create: `js/import/mfg-direct/ocr-overlay/ocr-overlay-panel.js`
- Modify: `js/import/mfg-direct/mfg-direct-panel.js`

- [ ] **Step 1: Implement `ocr-overlay-panel.js`**

Responsibilities (wire DOM to the pure state, re-render on change):
- `openOverlay(payload, { onConfirm })`: build state via `createState`, render modal into `document.body`, bind events, store `onConfirm`.
- Token click → `selectToken` (or `selectTokens` for a drag set) then `applyPending`; re-render.
- Cell click → `selectCell` then `applyPending`; re-render.
- Cell double-click → replace cell with an `<input>`, on blur/Enter `setCellValue`.
- Drag rubber-band on `.ocr-img-wrap`: pointerdown→move→up; compute intersected `.ocr-token`s by bounding box; `selectTokens(ids)`.
- `#ocr-prev`/`#ocr-next` → `setPage`.
- `#ocr-cancel` → remove modal.
- `#ocr-confirm` → validate vendor set; call `onConfirm(state.rows, vendor)`; remove modal.
- Esc → `clearPending`.
- Mount the existing mfg-direct vendor picker into `#ocr-vendor-mount` (reuse its render + handlers; do not rebuild vendor logic).

Use realistic re-render (replace `#ocr-overlay` innerHTML and re-bind), matching the
existing mfg-direct `rerender()` pattern.

- [ ] **Step 2: Route image/PDF inputs to the overlay in `mfg-direct-panel.js`**

In `handleSourceFile(file)`: if the file extension is an image or `.pdf`, call
`apiMfgDirect.ocrOverlayB64(b64, file.name, state.scanTemplate || 'generic')` and
`openOverlay(payload, { onConfirm: (rows, vendor) => { /* set state.lineItems = rows,
state.vendor = vendor, state.sourceFile = {name,bytes:b64}; then call the existing
importPO() */ } })`. CSV/XLS keep the current path. Keep the existing flat editor as the
fallback if `ocr_overlay_b64` returns no pages or errors (AppLog.warn).

In `window._scanReceived` (registered in app-init.js): when the payload includes `pages`
(phone scan now returns the overlay payload — Task 7), open the overlay instead of filling
the flat table. Keep backward-compatible handling if `pages` is absent.

- [ ] **Step 3: Manual sanity + lint**

Run: `npx eslint js/ && npx tsc --noEmit && npx vitest run` → pass
(Behavioral verification is the E2E task.)

- [ ] **Step 4: Commit**

```bash
git add js/import/mfg-direct/ocr-overlay/ocr-overlay-panel.js js/import/mfg-direct/mfg-direct-panel.js
git commit -m "feat(import): wire OCR overlay modal into mfg-direct drop + scan flows"
```

---

## Task 7: Phone-scan path returns the overlay payload

**Files:**
- Modify: `pnp_server.py` (the `_handle_scan_upload` handler)
- Test: extend `tests/python/test_scan_session.py`

- [ ] **Step 1: Update the upload handler**

Where it currently calls `parse_source_file_b64` and pushes `window._scanReceived(...)`,
also build the overlay payload via `self.server.api.ocr_overlay_b64(image_b64, filename,
template)` and include `pages` + `prefill_rows` in the `_scanReceived` payload (keep
`line_items` for backward compatibility, or set `line_items = prefill_rows`). Keep the
size/extension/base64 validation already there.

- [ ] **Step 2: Extend the scan-session test**

Add a test asserting the `_scanReceived` push payload now contains `pages` with at least
one page dict (use the fake-api pattern already in `test_scan_session.py`; have the fake
`api.ocr_overlay_b64` return a canned overlay).

- [ ] **Step 3: Run + commit**

Run: `python -m pytest tests/python/test_scan_session.py -q` → pass
```bash
git add pnp_server.py tests/python/test_scan_session.py
git commit -m "feat(scan): phone upload returns OCR overlay payload"
```

---

## Task 8: End-to-end Playwright

**Files:**
- Create: `tests/js/e2e/ocr-overlay.spec.mjs`

Realistic interactions only (no dispatchEvent/force — project policy). Reuse the mocked
pywebview harness (`helpers.mjs`); stub `ocr_overlay_b64` to return a fixture payload (a
tiny 1x1 PNG `image_b64`, two word tokens, two prefill rows), and stub
`create_purchase_order_with_items` (record args).

- [ ] **Step 1: Write the spec**

Cover, with real `.click()`/`.dblclick()`/drag (`mouse.down/move/up`)/`.fill()`:
1. Drop/select an image → modal `#ocr-overlay` appears with the image and `.ocr-token`s.
2. Click a token then a cell → cell text updates (word-first).
3. Click a different cell then a token → updates (cell-first).
4. Double-click a cell, type a value, blur → cell shows typed value.
5. (multi-page fixture) `#ocr-next` switches the page image.
6. Set vendor via the mounted picker, click `#ocr-confirm` → assert
   `create_purchase_order_with_items` was called with the corrected rows.

- [ ] **Step 2: Run**

Run: `npx playwright test ocr-overlay` → all pass.
Run the quality suite to confirm no regression: `npx playwright test --project=quality` (sticky-buttons/resize-visibility unaffected).

- [ ] **Step 3: Commit**

```bash
git add tests/js/e2e/ocr-overlay.spec.mjs
git commit -m "test(import): E2E for OCR overlay modal assignment + import"
```

---

## Final verification (run all gates before PR)

```bash
ruff check .
python -m pytest tests/python/ -q
npx eslint js/
npx tsc --noEmit
npx vitest run
npx playwright test
python scripts/gen-code-map.py && git diff --quiet docs/code-map.md || (git add docs/code-map.md && git commit -m "chore(code-map): regenerate for OCR overlay modules")
python scripts/check-layout-tokens.py --check
python scripts/check-manifests.py
```

Then push + PR via `bash scripts/push-pr.sh`, watch CI to green, merge.

## Notes / gotchas (learned this session)

- **Regenerate `docs/code-map.md`** (new modules) or CI's "Verify code map" fails.
- **Layout-token guard** scans `css/` files for raw px in layout props — put static dims in
  `css/tokens.css` as `--ocr-*` vars; inline `%` positions in JS strings are not scanned.
  If you add raw px to a `css/` file, expect to baseline it in `scripts/check-layout-tokens.ignore`.
- **Rebase before merge** — `origin/main` moves fast (parallel work); the PR may revert merged
  work if built on a stale base. Rebase onto current `origin/main`, resolve, re-run gates.
- Do **not** commit `data/signal-*.jpeg` (PII; locally excluded). Tests use synthetic fixtures.
- Confirm the exact `InventoryApi` constructor and the `distributor_profiles` text-parse entry
  point names against the real files before finalizing Tasks 3.
```
