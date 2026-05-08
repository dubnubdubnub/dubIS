# bom — BOM panel: CSV loading, match display, consume, and staging editor

## Owns

Parses and displays the BOM CSV, runs BOM-to-inventory matching, and manages the
staging editor for raw row edits. Owns BOM file state, raw rows, column mappings,
and consume workflow. Does NOT own inventory data (owned by `store.js`) or the
inventory comparison view (rendered by `js/inventory/inv-bom-mode.js`).

## Emits

- `BOM_LOADED` — emitted from `bom-panel.js:emitBomData` when a BOM is loaded/reprocessed
- `BOM_CLEARED` — emitted from `bom-events.js:setupEvents` when the Clear button is clicked

## Listens

- `INVENTORY_LOADED` — `bom-events.js:setupEvents`; auto-loads last BOM file from preferences
- `INVENTORY_UPDATED` — `bom-events.js:setupEvents`; re-runs matching and re-renders
- `CONFIRMED_CHANGED` / `LINKS_CHANGED` — `bom-events.js:setupEvents`; re-runs matching
- `LINKING_MODE` — `bom-events.js:setupEvents`; re-renders panel with linking banner
- `SAVE_AND_CLOSE` — `bom-events.js:setupEvents`; saves BOM CSV then confirms close

## Public exports

- `bom-panel.js`: `init` — mount point; wires drop zone, consume modal, and event handlers
- `bom-logic.js`: `classifyBomRow`, `countBomWarnings`, `computeRows`, `buildStatusMap`, `buildLinkableKeys`, `prepareConsumption`, `computePriceInfo` — pure BOM logic
- `bom-renderer.js`: `renderDropZone`, `renderLoadedDropZone`, `renderBomSummary`, `renderPriceInfo`, `renderLinkingBanner`, `renderStagingHead`, `renderStagingRow` — pure HTML renderers
- `bom-state.js`: `ConsumeState` — panel state singleton (default export is the mutable state object)
- `bom-events.js`: `setupEvents` — wires all DOM listeners and EventBus subscriptions

## Imports from

- `../event-bus.js`, `../store.js`, `../api.js`, `../ui-helpers.js`
- `../part-keys.js` — bomKey, invPartKey, rawRowAggKey, bomAggKey, countStatuses
- `../csv-parser.js` — processBOM, aggregateBomRows, generateCSV, isDnp, extractPartIds
- `../matching.js` — matchBOM
- `../undo-redo.js` — UndoRedo

## Internal layout

- `bom-panel.js` — thin init + render coordinator; owns match/emit cycle
- `bom-events.js` — all DOM and EventBus listener wiring
- `bom-state.js` — mutable panel state singleton
- `bom-logic.js` — pure BOM logic (row classify, status, price, consume prep)
- `bom-renderer.js` — pure HTML renderers (drop zone, summary, staging table)
