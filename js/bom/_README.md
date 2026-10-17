# bom — BOM panel: CSV loading, match display, consume, and staging editor

## Owns

Parses and displays the BOM CSV, runs BOM-to-inventory matching, and manages the
staging editor for raw row edits. Owns BOM file state, raw rows, column mappings,
and consume workflow. Does NOT own inventory data (owned by `store.js`) or the
inventory comparison view (rendered by `js/inventory/inv-bom-mode.js`).

## Emits

- `BOM_LOADED` — emitted from `bom-panel.js:emitBomData` when a BOM is loaded or the user changes it (an edit, the multiplier, a link)
- `BOM_REMATCHED` — emitted from `bom-panel.js:emitBomData` for a re-match nobody asked for (an inventory refresh, a spec lookup landing); not a panel reopen trigger
- `BOM_CLEARED` — emitted from `bom-events.js:setupEvents` when the Clear button is clicked

## Listens

- `INVENTORY_LOADED` — `bom-events.js:setupEvents`; auto-loads last BOM file from preferences
- `INVENTORY_UPDATED` — `bom-events.js:setupEvents`; re-runs matching and re-renders
- `CONFIRMED_CHANGED` / `LINKS_CHANGED` — `bom-events.js:setupEvents`; re-runs matching
- `LINKING_MODE` — `bom-events.js:setupEvents`; re-renders panel with linking banner
- `SAVE_AND_CLOSE` — `bom-events.js:setupEvents`; saves BOM CSV then confirms close

## Public exports

- `bom-panel.js`: `init` — mount point; wires drop zone, consume modal, and event handlers
- `bom-logic.js`: `classifyBomRow`, `countBomWarnings`, `computeRows`, `buildStatusMap`, `buildLinkableKeys`, `prepareConsumption`, `buildMissingCartEntries`, `computePriceInfo` — pure BOM logic. Every "which statuses are X" question is answered by the status registry `../bom-status.js`, never a local list
- `board-features.js`: `boardFeatureReason`, `rawRowBoardReason`, `hasPartNumber`, `EXCLUDED_FROM_BOM_REASON` — is a BOM line a board feature (fiducial, mounting hole, bare test pad, Tag-Connect, "Exclude from BOM") rather than a part; drives the `board-only` status
- `bom-renderer.js`: `renderDropZone`, `renderLoadedDropZone`, `renderBomSummary`, `renderPriceInfo`, `renderLinkingBanner`, `renderStagingHead`, `renderStagingRow` — pure HTML renderers
- `bom-spec-lookup.js`: `createSpecLookup`, `codesNeedingSpec`, `specFromProduct`, `isNotFoundError`, `IDENTITY_MATCH_TYPES` — pure LCSC spec lookup for unidentified rows (fetcher injected; session cache)
- `bom-state.js`: `ConsumeState` — panel state singleton (default export is the mutable state object)
- `bom-events.js`: `setupEvents` — wires all DOM listeners and EventBus subscriptions

## Imports from

- `../event-bus.js`, `../store.js`, `../api.js`, `../ui-helpers.js`
- `../part-keys.js` — bomKey, invPartKey, rawRowAggKey, bomAggKey, countStatuses
- `../bom-status.js` — the effective-status registry (statusHas, BOM_BUCKETS)
- `../csv-parser.js` — processBOM, aggregateBomRows, generateCSV, isDnp, extractPartIds
- `../matching.js` — matchBOM
- `../undo-redo.js` — UndoRedo

## Internal layout

- `bom-panel.js` — thin init + render coordinator; owns match/emit cycle and the spec-lookup → passive re-match
- `bom-spec-lookup.js` — asks LCSC what an unidentified row's code is, so matching can read `bom.spec`
- `bom-events.js` — all DOM and EventBus listener wiring
- `bom-state.js` — mutable panel state singleton
- `bom-logic.js` — pure BOM logic (row classify, status, price, consume prep)
- `board-features.js` — pure board-feature classification (board-only rows)
- `bom-renderer.js` — pure HTML renderers (drop zone, summary, staging table)
