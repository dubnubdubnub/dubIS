# import — Purchase import panel: CSV column mapping, staging, and manufacturer-direct flow

## Owns

Handles import of purchase CSV files: parsing, column-to-field mapping, row
validation, and committing rows to inventory. Also hosts the manufacturer-direct
PO editor sub-flow (`mfg-direct/`). Does NOT own the inventory store (updated via
`store.onInventoryUpdated`) or BOM data.

## Public exports

- `import-panel.js`: `init` — mount point; wires drop zone, column mapper, and undo/redo
- `import-logic.js`: `TARGET_FIELDS`, `PART_ID_FIELDS`, `PO_TEMPLATES`, `classifyRow`, `countWarnings`, `transformImportRows`, `validateImportData` — pure import logic
- `import-renderer.js`: `renderDropZone`, `renderMapper`, `renderStagingTable`, `renderTemplateLink` — pure HTML renderers
- `mfg-direct/mfg-direct-panel.js`: `openOcrImport`, `startPhoneScan`, `editPO` — image/PDF OCR import, phone-scan session, and PO editor entry points
- `mfg-direct/mfg-direct-logic.js`: `canonicalizeUrl`, `emptyLineItem`, `validateLineItems`, `formatMatchBadge` — pure PO logic
- `mfg-direct/mfg-direct-renderer.js`: `renderEditor` — pure PO editor HTML

## Imports from

- `../store.js` — onInventoryUpdated, savePreferences, store.preferences
- `../api.js` — api(), AppLog
- `../ui-helpers.js` — showToast, escHtml, setupDropZone, resetDropZoneInput
- `../csv-parser.js` — parseCSV, generateCSV
- `../undo-redo.js` — UndoRedo

## Internal layout

- `import-panel.js` — thin init + render coordinator; owns undo/redo for import
- `import-logic.js` — pure logic: row classify, column mapping, row transform
- `import-renderer.js` — pure HTML: drop zone, column mapper, staging table
- `mfg-direct/mfg-direct-panel.js` — PO editor wiring and API calls
- `mfg-direct/mfg-direct-logic.js` — pure PO validation and badge formatting
- `mfg-direct/mfg-direct-renderer.js` — pure PO editor HTML renderer
