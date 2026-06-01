# Split the import panel into two flows (CSV vs Image/PDF/Phone)

**Status:** Approved design (brainstorming). User asked to proceed straight to implementation.
**Date:** 2026-06-01

## Context

The OCR-overlay PO import feature (PR #272) is only reachable behind a small "★ Direct
from mfg" button anchored inside the single CSV drop zone. Users dragging an image onto
the main drop zone get nothing (it only accepts CSV and routes to the text parser), and
the file picker offers no image/PDF option. The "Direct from mfg" concept is also an
unnecessary separate flow: importing a manufacturer's image/PDF should use the **same OCR
system** as everything else — it's just the OCR flow with the **Generic** template (no
distributor) instead of a distributor template (LCSC/DigiKey/Mouser/Pololu).

This redesign splits the import panel into two clear, side-by-side flows and removes the
"Direct from mfg" button and the standalone mfg-direct editor as a primary surface.

## Goals

- Two side-by-side drop zones in the import panel header:
  - **CSV / TSV / TXT / XLS** → existing inline column-mapping + editable staging → import (no modal).
  - **Image / PDF / Phone** → the existing OCR review modal.
- Remove the **★ Direct from mfg** button and `startDirectFlow`'s standalone editor UI.
- The image/PDF/phone zone has a **template dropdown defaulting to "Generic — direct from mfg"** (plus LCSC/DigiKey/Mouser/Pololu); the chosen template seeds the OCR modal.
- A **"📷 Scan with phone"** button in the image zone (existing `start_scan_session` QR flow), result opens the same modal.
- **Manual entry** (hand-typed line items, no file) lives on the **CSV side** (blank-PO templates + editable staging "+ add row"), not the image side.
- **Generic template:** (a) clearer labeling that Generic = "direct from a manufacturer, no distributor," and (b) a smarter generic OCR heuristic for free-form manufacturer invoices.
- Reuse the existing OCR-overlay machinery (`ocr_overlay_b64`, `openOverlay`, `importPO`, shared `vendor-picker.js`) — do not rebuild it.

## Non-goals

- No change to PO storage, `create_purchase_order_with_items`, or the OCR modal's internal behavior (assignment, drag, edit, page nav) — only its entry points change.
- No new distributor templates; no added Generic grid columns (fields stay as today).
- No removal of the existing column-mapping/staging logic for CSV.

## UX

**Import panel header — two zones side by side** (stack on narrow widths):

- **Left: CSV / TSV / TXT / XLS**
  - "Drop a purchase CSV here / or click to browse" — file input `accept=".csv,.tsv,.txt,.xls"`.
  - Drop/browse → existing `handleImportFile` → `loadImportText` → column mapper + editable staging → import. Unchanged.
  - "create blank PO:" template buttons (Generic/LCSC/DigiKey/Pololu/Mouser) — unchanged (`createNewPO`).
  - **"+ add row manually"** → opens the editable staging grid with one blank row (no file), reusing the existing staging table; user types rows and imports. (Manual entry moved here.)

- **Right: Image / PDF / Phone**
  - **Template dropdown** (`#import-ocr-template`), default `generic` labeled "Generic — direct from mfg"; options LCSC/DigiKey/Mouser/Pololu. One-line hint under it: "Generic = a manufacturer invoice with no distributor packing list."
  - "Drop image / PDF here / or click to browse" — file input `accept=".png,.jpg,.jpeg,.pdf"`.
  - **"📷 Scan with phone"** button.
  - Drop/browse an image or PDF → `openOcrImport(file, template)`; scan button → `startPhoneScan(template)`. Both open the existing OCR review modal seeded with the selected template; on confirm the modal hands rows + vendor to the existing `importPO` path (photo stored as source).

## Architecture (reuse-first)

- **Import panel becomes the single owner of both zones.** A new two-zone renderer replaces
  `renderDropZone`. The left zone keeps today's wiring; the right zone is thin — it reads the
  template dropdown and delegates to OCR entry points.
- **Expose clean entry points from the mfg-direct module** (which already holds the overlay +
  `importPO` + vendor logic), so the import panel doesn't duplicate anything:
  - `openOcrImport(mountEl, file, template)` — read file → base64 → `apiMfgDirect.ocrOverlayB64(b64, name, template)` → `openOverlay(payload, {onConfirm})`; fall back to a clear error/toast if OCR yields no pages.
  - `startPhoneScan(mountEl, template)` — `apiMfgDirect.startScanSession(template)` → render the QR/URL modal (existing scan modal) → on `window._scanReceived` with pages → `openOverlay`.
  These wrap logic that currently lives inside `startDirectFlow`/`handleSourceFile`; refactor those internals into reusable functions and **delete** the standalone editor render path and the `state.scanTemplate` UI that lived in the old editor.
- **Remove** the `★ Direct` button from `renderDropZone`/`import-panel.js` and the
  `data-template === 'direct'` branch. Keep `createNewPO` (blank CSV templates).
- **Phone-scan templates:** the existing `window._scanReceived` handler already opens the
  overlay when `payload.pages` is present (from the prior feature). The desktop-side scan
  modal (template select + QR) currently lives in mfg-direct; reuse it via `startPhoneScan`.

## Generic OCR improvements

- **Labeling:** template option text + hint as above; the OCR modal header and any empty
  state reflect "Generic (direct from mfg)".
- **Smarter generic heuristic** in `distributor_profiles` (the `generic` profile / the
  fallback used by `mfg_direct_import._parse_text_with_template` → `_heuristic_parse_lines`):
  improve free-form manufacturer-invoice extraction where there is no distributor-PN anchor —
  e.g.:
  - recognize an MPN-like token even when it is not the first token on the line;
  - detect qty and unit-price columns by position/format (trailing integer + trailing
    decimal-or-integer price) rather than requiring a fixed pattern;
  - capture a manufacturer-name token when present;
  - tolerate label noise ("Qty", "Unit Price", "$").
  Keep it heuristic (the user reviews/edits in the modal); add unit tests with representative
  free-form invoice lines, including ones the current heuristic misses.

## Files (anticipated)

- `js/import/import-renderer.js` — new two-zone `renderDropZone` (CSV zone + image zone with template dropdown, scan button); drop the ★ button.
- `js/import/import-panel.js` — wire both zones; route CSV→`handleImportFile`, image/PDF→`openOcrImport`, scan→`startPhoneScan`; "+ add row manually"; remove the `direct` branch.
- `js/import/mfg-direct/mfg-direct-panel.js` — extract/export `openOcrImport`, `startPhoneScan`; remove the standalone editor entry (`startDirectFlow` editor UI) while keeping `importPO`, the overlay open, the scan modal, and vendor logic.
- `js/import/mfg-direct/mfg-direct-renderer.js` — remove/trim the old editor markup + the in-editor scan affordance now superseded by the import-panel zone (keep the scan QR modal + overlay renderers).
- `distributor_profiles.py` (and/or `mfg_direct_import.py`) — smarter generic heuristic + tests.
- `index.html` / CSS — two-zone layout styling (reuse existing tokens; pass the layout-token guard).
- Tests: vitest (zone routing, template default), python (generic heuristic), Playwright E2E (new entry points; remove ★-button assumptions).

## Error handling

- Prefer throwing / `AppLog.warn` over silent failure. If OCR returns no pages or errors,
  toast a clear message and leave the user on the import panel (no half-open modal).
- Image/PDF dropped on the CSV zone (or vice-versa): detect by extension and route correctly;
  if a wrong type is dropped, show a short toast pointing to the correct zone.

## Testing

- **vitest:** the routing decision (extension → CSV vs OCR), template-default = generic, "+ add row" produces an empty editable staging row. Keep pure logic testable.
- **Python:** new generic-heuristic tests (free-form invoice lines → MPN/manufacturer/qty/price), including cases the old heuristic missed; existing distributor-profile tests stay green.
- **Playwright E2E (realistic interactions only):** ★ button gone; CSV drop → inline staging; image drop on the right zone → OCR modal opens (reuse the mocked `ocr_overlay_b64`); template dropdown defaults to Generic; "📷 Scan with phone" opens the QR modal; manual "+ add row" works. Update/replace the existing mfg-direct/ocr-overlay/scan specs to the new entry points; do not weaken sticky-buttons/resize-visibility.

## Test asset note

`data/signal-*.jpeg` (real LCSC packing list) is local-only for manual testing (git-excluded, PII). Automated tests use synthetic fixtures.
