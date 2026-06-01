# OCR-overlay PO import — Live-Text-style review modal

**Status:** Approved design (brainstorming) — ready for implementation plan
**Date:** 2026-05-31

## Context

The phone-camera PO scanning feature (PRs #269, #271) drops a photographed/scanned
Purchase Order into the existing "Direct from mfg" import flow, where a heuristic OCR
parser (`distributor_profiles.py`) auto-fills an editable line-item table. On real,
messy scans the heuristic frequently mis-reads or misses fields, and the current
editor gives the user no way to see *where* on the page a value came from — they just
get a text grid with no image reference.

This feature reworks that review step into an **Apple Live-Text-style side-by-side
modal**: the scan on the left with every OCR word/line rendered as a clickable
overlay, and the auto-filled template grid on the right. The user fixes mistakes by
clicking a word on the image and a cell in the grid (either order), drag-selecting
multiple words for long descriptions, or double-clicking a cell to type directly.
The image makes corrections fast and verifiable.

This is purely a **review/correction UX layer** over OCR output that already exists —
it does not change how POs are stored. On confirm it hands corrected rows to the
existing import path (vendor pick → create PO with the source file saved).

## Goals

- Side-by-side modal: scanned page(s) left, auto-filled multi-row grid right.
- OCR words **and** lines rendered as positioned, clickable overlay tokens on the image.
- Auto-fill the grid first (existing heuristic); manual click-to-assign is the correction safety net.
- Bidirectional fill: click word→cell **or** cell→word completes an assignment.
- Drag across multiple words/lines to combine into one value (long descriptions).
- Double-click a cell to type/adjust the value manually.
- Low-confidence auto-fills flagged for review (amber); parser-missed cells shown blank.
- Multi-page support: PDFs rasterized to images (all pages) with page navigation; image files supported natively.
- Unified entry: desktop image/PDF drops **and** phone-scanned photos open this same modal.
- On confirm, hand corrected rows to the existing import (vendor + create-PO-with-source).

## Non-goals

- No change to PO storage schema (`purchase_ledger.csv` / `purchase_orders.csv`) or the create-PO API.
- No automatic table-structure detection beyond the existing heuristic pre-fill (we surface tokens; the user assigns).
- No OCR engine change — still Tesseract via `pytesseract`.
- No new design-token system; reuse existing CSS variables (see Styling).

## UX & interaction

**Trigger:** dropping an image (`.png/.jpg/.jpeg`) or PDF in the mfg-direct drop-zone,
or receiving a phone scan, opens the review modal (replaces the current line-item editor
as the review step). **CSV/XLS drops keep the existing flat flow** — they have no image to
overlay, so they bypass the modal entirely.

**Layout:** a wide modal (`.modal-overlay` + `.modal modal-wide`). Header shows template
name and, for multi-page docs, `‹ Page n / N ›` navigation. Body is a two-pane split:

- **Left — scan pane:** the current page image, with OCR tokens absolutely positioned
  over it as buttons (word tokens; line tokens selectable as a unit). Selected tokens are
  outlined. Drag (pointer down on the image, move, up) rubber-band-selects the words/lines
  intersected, combining their text in reading order.
- **Right — grid pane:** the auto-filled multi-row staging grid, columns = the chosen
  distributor template's fields. Cell states: normal, **active drop-target** (highlighted),
  **low-confidence** (amber), **blank/missing**. Double-click → inline `<input>` for manual edit.

**Assignment (bidirectional):**
- Click a token (or drag-select several) → it becomes the pending *source*; grid cells
  highlight as drop targets → click a cell to fill it.
- Or click a cell → it becomes the pending *target*; click a token (or drag-select) to fill it.
- Pending selection is visually indicated and cleared on completion or Esc.

**Vendor selection:** because this modal replaces the editor that currently hosts the vendor
picker, the modal includes the **existing vendor-picker component** (reused, not rebuilt) —
e.g. in the grid pane's header or a footer bar — so the user can set the vendor without
leaving the modal.

**Confirm:** "Continue to import" hands the corrected rows + selected vendor to the existing
`importPO()` path (`create_purchase_order_with_items`, source file stored). It blocks (with a
clear prompt) if no vendor is set, matching the current editor's behavior. "Cancel" returns
to the drop-zone.

## Styling (hard requirement)

The modal MUST match the rest of the app: reuse existing CSS custom properties
(`--bg-surface`, `--bg-input`, `--bg-tertiary`, `--border-default`, `--color-blue`,
`--color-yellow`, `--text-primary`, `--text-secondary`, `--text-muted`, `--font-mono`, …)
and existing modal conventions (`.modal-overlay`, `.modal`, `.modal-wide`). Dark-theme
consistent. The mockup's ad-hoc light colors are illustrative only. New CSS lives in the
existing CSS structure (e.g. a dedicated `css/components/` or panel file) and must pass the
`check-layout-tokens` guard (no new hard-coded px layout values beyond the established
raw-px+baseline pattern; prefer tokens where they exist).

## Architecture & components

### Backend

1. **PDF rasterization** — add **PyMuPDF (`fitz`)** to `requirements.txt` (single pip
   dependency, no system Poppler). New helper renders every PDF page to a PNG (reasonable
   DPI, e.g. 150–200) returning page images + pixel dimensions. Image files pass through
   as a single "page".

2. **OCR layout extraction** — new module (e.g. `ocr_layout.py`) using
   `pytesseract.image_to_data(..., output_type=DICT)` to produce, per page:
   - `words`: `[{text, x, y, w, h, conf, block, par, line, word}]` (pixel coords)
   - `lines`: derived by grouping words sharing `(block, par, line)` → `{text, x, y, w, h, conf}`
   Confidence preserved per word/line for the amber flag.

3. **Pre-fill** — reuse `distributor_profiles` / `mfg_direct_import` heuristic on the OCR
   text to produce the initial grid rows, tagging each filled value with the confidence of
   the source word(s) where determinable.

4. **API method** (in `inventory_api.py`), e.g.
   `ocr_overlay_b64(file_b64, filename, template) -> dict`:
   ```
   {
     "pages": [
       { "image_b64": <png>, "width": int, "height": int,
         "words": [{text,x,y,w,h,conf,line_id}, ...],
         "lines": [{text,x,y,w,h,conf}, ...] }
     ],
     "prefill_rows": [ {<template-field>: value, ...,
                        "_low_conf": ["<field>", ...]} ],
     "template": "lcsc"
   }
   ```
   Coordinates are pixel-relative to each page's `width`/`height`; the frontend positions
   tokens as percentages so they scale with the displayed image.

   Phone path: `pnp_server`'s `/api/scan/upload` can call this same method and push the
   richer payload to `window._scanReceived` (or a new push) so phone scans open the modal too.

### Frontend (vanilla ES modules, no build step)

New module group under `js/import/mfg-direct/ocr-overlay/` (or `js/import/ocr-overlay/`),
kept in small focused files:

- **`ocr-overlay-state.js`** (pure) — selection model (pending source/target, drag set),
  row/cell data, page index, low-conf flags; transforms for token→cell assignment and
  drag-combine. Unit-testable without DOM.
- **`ocr-overlay-renderer.js`** — builds the modal DOM: split panes, image + positioned
  token buttons (positions as % of page size), grid (reuse the existing line-item table
  markup + double-click edit), page nav. Uses app CSS tokens.
- **`ocr-overlay-panel.js`** — event wiring: token clicks, drag rubber-band, cell clicks,
  double-click-to-edit, page nav, confirm/cancel; calls the API and bridges to the existing
  `importPO()` on confirm.
- Hook into `mfg-direct-panel.js` `handleSourceFile` / `window._scanReceived` so image/PDF
  inputs open this modal instead of the flat editor.

## Data flow

```
drop image/PDF  OR  phone scan
        │
        ▼
inventory_api.ocr_overlay_b64(file_b64, filename, template)
   ├─ rasterize (PyMuPDF) → page PNG(s) + dims
   ├─ pytesseract.image_to_data → words/lines + confidence
   └─ distributor heuristic → prefill_rows (+ low-conf flags)
        │
        ▼
overlay modal: image + token buttons (left) | auto-filled grid (right)
   user assigns/corrects (click word↔cell, drag-combine, dbl-click edit, page nav)
        │ confirm
        ▼
existing importPO() → create_purchase_order_with_items (source file stored)
```

## Error handling

- Throw/`AppLog.warn` over silent failure (project policy). OCR/raster failures return a
  clear error; the modal shows a message and offers the plain manual-entry fallback (the
  current blank grid) so import is never fully blocked.
- Malformed/oversized uploads already validated on the phone path; the desktop path decodes
  locally.
- Tesseract/PyMuPDF must be real dependencies in `requirements*.txt` — **no `pytest.skip`/
  `importorskip`** to hide them (project policy); system-binary guards (tesseract) follow the
  existing pre-existing-skip pattern only.

## Testing

- **Python:** `ocr_layout` unit tests (synthetic rendered image → expected words/lines +
  bbox + confidence ordering); PDF rasterization test (tiny generated PDF → N page images
  with sane dims); `ocr_overlay_b64` API test (fake image → pages + prefill shape). Use
  generated fixtures, **not** the real PII packing list.
- **JS (vitest):** `ocr-overlay-state` pure-logic tests — token→cell assignment (both
  directions), drag-combine ordering, low-conf flagging, page switching.
- **E2E (Playwright, realistic interactions only — no dispatchEvent/force):** open the modal
  for a fixture image, real-click a token then a cell (and the reverse), real drag-select to
  combine, double-click a cell and type, page-nav for a multi-page fixture, confirm →
  assert rows reach the import and the source file is stored. Reuse the real-server harness
  pattern from `scan-capture.spec.mjs` where a backend is needed.
- Regenerate JS fixtures (`scripts/generate-test-fixtures.py`) only if backend shapes that
  feed existing fixtures change (not expected).

## Test asset note

`data/signal-*.jpeg` (real LCSC packing list) is for **manual testing only** and is
git-ignored locally (`.git/info/exclude`) — it contains PII and must never be committed.
Automated tests use synthetic fixtures.

## Deferred / future

- Smarter automatic row/column table detection (use Tesseract block/line geometry to map
  tokens to grid cells without manual clicks).
- Per-token confidence heatmap toggle.
- Re-running OCR at higher DPI on a user-selected region.
