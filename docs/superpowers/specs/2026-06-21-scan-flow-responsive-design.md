# Responsive, Unified Scan / OCR Import Flow — Design

**Date:** 2026-06-21
**Branch:** `claude/scan-flow-responsive`
**Status:** Approved design, pre-implementation

## Problem

The image-import flow has five rough edges:

1. **No feedback on input.** Dropping an image (or clicking the zone → OS file
   picker → choosing a file) runs `_fileToB64` → `ocrEngineAvailable` →
   `await ocrOverlayB64(...)` with **nothing visible happening**. For the VLM
   backend this is several seconds of dead air — it feels like Windows ate the
   input.
2. **Two divergent flows.** Drag/browse goes through `openOcrImport` (single
   file, silent, blocking → overlay). Phone goes through `scanReceived` (multi,
   with an immediate `_scanReceiving` ack → grouping editor or overlay). They
   should converge once images are in.
3. **No multi-image for drag/browse.** `openOcrImport` and the OCR file input
   handle exactly one file; only the phone path supports multiple photos.
4. **OCR highlighting ignores the detecting model.** The overlay's clickable
   token boxes always come from Tesseract `image_to_data()`, even when the VLM
   produced the line items. Highlights are disconnected from what actually read
   the text.
5. **Templates can't change after upload, barely affect OCR, and don't prefill
   the vendor.** The template (`lcsc`, `digikey`, …) is picked before upload.
   For the VLM path it has **zero** effect on extraction (the prompt never sees
   it) — it only routes the distributor PN into a column afterward. And the
   template is wholly disconnected from the PO **vendor**: picking the "LCSC"
   template does not prefill the LCSC vendor; that stays a manual entry.

## Goals

- Dropping/browsing/photographing an image gives **instant** on-screen
  acknowledgement before any OCR runs.
- Drag-drop, click-to-browse (OS picker), and phone upload all converge on **one
  downstream flow**: 1 image → OCR overlay; 2+ images → grouping editor.
- Multi-image works for drag-drop and the OS file picker, not just the phone.
- OCR highlighting reflects the model that actually detected each row (VLM boxes
  when VLM extracted it; Tesseract boxes otherwise).
- The template can be switched **after** upload; switching re-routes the
  distributor-PN column, re-parses cached OCR text, and **auto-prefills the
  matching vendor**. The VLM prompt is also made template-aware.

## Non-Goals

- No change to the CSV/TSV import flow (`import-drop-zone`).
- No change to the phone capture page UI itself (grouping on the phone stays).
- No new cloud OCR backend; VLM stays local-only (Ollama) and self-gating.
- No automatic re-run of the (slow) VLM on every template switch — that is an
  explicit, opt-in button.

## Design

### 1. Unified entry point + immediate shell

Introduce a shared router in `mfg-direct-panel.js` that all three entries feed:

```
drag-drop ────────┐
click → OS picker ─┼─▶ beginScanImport(files)  ── opens shell IMMEDIATELY,
                   │                               then OCRs each file
phone upload ──────┘─▶ scanReceived(payload)   ── already OCR'd server-side
                                  │
                          routeScanResult(photos, groups, template)
                                  ├─ 1 photo  ─▶ openOverlay(...)
                                  └─ 2+ photos ─▶ openGroupingEditor(...)
```

- **`beginScanImport(files, template)`** (new) is the single drag/browse entry.
  It replaces the body of `openOcrImport` and accepts a `FileList`/array.
  - **First action, synchronously:** open the immediate shell modal showing one
    thumbnail tile per dropped file with a per-tile "Reading…" spinner. This
    happens *before* `_fileToB64` and *before* the `ocrEngineAvailable` check, so
    the user sees a response within one frame of the drop/selection.
  - Then, for each file: read b64, call `ocrOverlayB64(b64, name, template)`,
    and **stream** the result into that file's tile (spinner → ✓ / row count) as
    it completes. Files are OCR'd sequentially (the pywebview bridge is one call
    at a time); each completion updates its tile so progress is visible.
  - On OCR error for a file, mark that tile failed (keep the others); reuse the
    existing Tesseract-missing / "no text found" toasts.
- **`routeScanResult(photos, groups, template)`** (new, extracted from the
  current `scanReceived` body) is the shared downstream:
  - `photos.length > 1` → `openGroupingEditor(photos, groups, template, …)`.
  - else → `openOverlay(payload, …)`.
  Both `beginScanImport` (after all files OCR'd) and `scanReceived` call it, so
  the phone and desktop paths are identical from here on.
- `_scanReceiving` (phone's immediate ack) and the new shell share one render
  helper so the acknowledgement looks the same regardless of source.

**Wiring changes** (`import-panel.js`):
- The OCR zone `dropCallback` passes **all** dropped files:
  `(files) => beginScanImport(body, files, ocrTemplate())`.
- `setupDropZone` is updated so the OCR zone hands the full file list to its
  callback (the CSV zone keeps single-file behavior).
- `#import-ocr-input` gains the `multiple` attribute; its `change` handler passes
  `input.files` (plural).

### 2. Multi-image normalization

`beginScanImport` builds the **same payload shape the phone produces** so the
existing grouping/queue code is reused verbatim:

```js
// per file, after ocrOverlayB64:
{ index, filename, image_b64, pages, prefill_rows }
```

- 1 file → overlay payload `{pages, prefill_rows, template}` (as today).
- 2+ files → `photos: [...]`, `groups: [[0],[1],…]` (each its own PO by
  default), passed to `openGroupingEditor`, which already concatenates pages +
  prefill_rows per group and feeds `startImportQueue`.

### 3. VLM bounding boxes — highlight uses the detecting model

**Backend (`vlm_extract.py`):**
- Extend `_PROMPT` to request a bounding box per item, in normalized
  `[x, y, w, h]` (0–1000 grid, qwen2.5-VL's native grounding convention), e.g.
  `"bbox": [x, y, w, h]` "as fractions of image width/height ×1000".
- `_to_line_item` parses `bbox` defensively: accept a 4-number list, clamp to
  range, convert to pixel coords against the page size; drop a malformed/absent
  bbox to `None` (no throw). Add `"_backend": "vlm"` and `"bbox": [...]|null` to
  each row dict.

**Backend (`ocr_layout.py`):**
- Tag rows from the grid extractor `"_backend": "grid"` and the flat parser
  `"_backend": "flat"`. Grid rows already know their cell box → carry it as
  `bbox`; flat rows have no box (`bbox: null`).

**Frontend (`ocr-overlay-*`):**
- Each prefill row carries `_backend` and optional `bbox` (pixel space, same
  coordinate system as `pages[].words`).
- When a row/cell is focused, the highlight uses **the row's own `bbox`** if
  present (VLM or grid). If `bbox` is null (flat parser, or VLM omitted it),
  **fuzzy-match the row's field text to the Tesseract tokens** in `pages[].words`
  and highlight those — so highlighting always works.
- A small per-row backend indicator (e.g. a subtle "VLM"/"OCR" tag) so the user
  can see what read each row. Tokens drawn from a model's own boxes are visually
  distinguishable from Tesseract-token fallback highlights.

### 4. Switch templates after upload

**In-overlay / in-grouping template selector.** A template `<select>` is added
to the overlay (and grouping editor) header. Changing it:

- **Instant, no re-OCR:** re-run `parse_with_template` over the **cached** page
  text already on the payload, and re-route the distributor-PN column. The cached
  `pages` (Tesseract tokens) and any VLM rows are retained; only column routing +
  flat/grid re-parse run. This is cheap and matches the fact that the VLM prompt
  change does not affect already-extracted rows.
- **Auto-prefill the vendor** (the fix for "LCSC doesn't prefill the vendor"):
  a `TEMPLATE_VENDOR` map (`lcsc`→"LCSC", `digikey`→"DigiKey", `mouser`→"Mouser",
  `pololu`→"Pololu"; `generic`→none). On select/switch, resolve the vendor via
  the existing `vendor-picker` upsert-by-name (creating the record with its
  canonical URL if missing) and set it as the current vendor. `generic` leaves
  the current vendor untouched. Only auto-fill when the vendor field is empty or
  still equals a previously auto-filled distributor vendor (don't clobber a
  vendor the user typed by hand).
- **Opt-in "Re-scan with this template" button.** Triggers a fresh backend pass
  (`ocrOverlayB64` with the new template) — the only path that re-runs the
  VLM/grid with the template hint. Used when the upload was done as Generic and
  the user then identifies the distributor. Shows the same per-tile/spinner
  feedback while it runs.

**VLM template hint (`vlm_extract.py`).** Inject the distributor name + PN format
into the prompt when the template is a distributor (not generic), e.g.
`"This is an LCSC packing list; its catalogue part numbers look like C<digits>;
map them to distributor_pn."` Generic keeps the current distributor-agnostic
prompt. This improves the **initial** extraction and the opt-in re-scan.

### 5. Data shape summary

`prefill_rows[i]` gains:

| Field      | Meaning                                                        |
|------------|---------------------------------------------------------------|
| `_backend` | `"vlm"` \| `"grid"` \| `"flat"` — which extractor produced it  |
| `bbox`     | `[x, y, w, h]` pixel-space box, or `null` (flat parser)        |

Everything else (`mpn`, `distributor`, `distributor_pn`, `quantity`, …,
`_low_conf`) is unchanged. The overlay `pages[]` token data is unchanged.

## Affected Files

**Backend (Python):**
- `vlm_extract.py` — template-aware prompt; bbox in prompt + `_to_line_item`;
  `_backend`/`bbox` fields.
- `ocr_layout.py` — tag `_backend` on grid/flat rows; carry grid cell `bbox`.
- `ocr_table.py` — expose per-row cell box for `bbox` (already detected
  internally).

**Frontend (JS):**
- `js/import/mfg-direct/mfg-direct-panel.js` — `beginScanImport` (multi-file +
  immediate shell + streaming), `routeScanResult` extraction, shared ack render.
- `js/import/import-panel.js` — OCR-zone wiring passes all files; `multiple` on
  input.
- `js/import/import-renderer.js` — `multiple` attribute on `#import-ocr-input`.
- `js/import/mfg-direct/ocr-overlay/ocr-overlay-renderer.js`,
  `ocr-overlay-state.js`, `ocr-overlay-panel.js`, `ocr-overlay-hittest.js` —
  per-row `_backend`/`bbox` highlight; backend indicator; in-overlay template
  select + "Re-scan" button; template→vendor prefill.
- `js/import/mfg-direct/scan-grouping.js` — in-grouping template select (shares
  the switch logic).
- A small shared module for `TEMPLATE_VENDOR` + `applyTemplateSwitch` reused by
  overlay and grouping editor.

## Testing

**Playwright E2E** (realistic interactions only — no `dispatchEvent`/`force`):
- Drop 1 image → shell appears immediately (assert shell visible before overlay)
  → overlay opens.
- Drop 3 images → shell with 3 tiles → grouping editor with 3 photos.
- Click-to-browse multi-select path reaches the same grouping editor.
- Switch template in the overlay → distributor-PN column re-routes **and** the
  vendor field fills with the matching distributor.
- "Re-scan with this template" triggers a fresh OCR pass.
- Respect existing `sticky-buttons.spec.mjs` / `resize-visibility.spec.mjs` for
  the responsive-layout piece — fix CSS if they fail, never weaken them.

**Python (`tests/python/`):**
- `_to_line_item`: bbox present → pixel conversion; bbox malformed/absent →
  `null`, row still emitted; `_backend` set.
- VLM prompt is template-aware (distributor hint present for `lcsc`, absent for
  `generic`).
- `ocr_layout` tags `_backend` correctly for grid vs flat.
- `template→vendor` resolution (existing vendor matched; missing vendor created).

**Regen + suites** (per project workflow):
- `python scripts/generate-test-fixtures.py` after backend row-shape changes.
- `npx eslint js/ && npx tsc --noEmit && npx vitest run`.
- `pytest tests/python/ -v`.

## Risks / Open Notes

- **VLM bbox accuracy varies.** Mitigated by the Tesseract-token fuzzy-match
  fallback whenever a bbox is missing or implausible.
- **Streaming UI complexity.** Sequential OCR with per-tile updates keeps it
  simple; no parallel bridge calls.
- **Don't clobber a hand-typed vendor.** Template auto-prefill only fills when
  the vendor is empty or was itself auto-filled.
