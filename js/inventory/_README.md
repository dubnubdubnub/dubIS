# inventory — Inventory panel: display, filter, sort, and BOM comparison

## Owns

Renders the full inventory panel in both normal and BOM-comparison modes. Owns
distributor/vendor filter state, column-sort/group state, and section collapse state.
Does NOT own BOM match data (produced by `js/bom/`) or generic-parts definitions.

## Listens

- `INVENTORY_LOADED` / `INVENTORY_UPDATED` / `PREFS_CHANGED` — `inv-events.js:setupEvents`; re-renders
- `BOM_LOADED` — `inv-events.js:setupEvents`; stores bomData, re-renders
- `BOM_CLEARED` — `inv-events.js:setupEvents`; clears BOM state, re-renders
- `LINKING_MODE` — `inv-events.js:setupEvents`; re-renders
- `FLYOUT_SEARCH_CHANGED` / `FLYOUT_ACTIVE_CHANGED` — `inv-events.js:setupEvents`; syncs search
- `FLYOUT_OPENED` / `FLYOUT_CLOSED` — `inv-events.js:setupEvents`; toggles drag-active CSS

## Public exports

- `inventory-panel.js`: `init` — mount point; wires DOM refs, events, and render
- `inv-events.js`: `setupEvents`, `isFlyoutDragActive` — event wiring + drag-state query
- `inv-state.js`: `hydrateFromPreferences` — restores sort/group view from preferences
- `inv-render.js`: `renderNormalInventory`, `renderGlobalScope`, `appendFlatRows`, `renderVendorPiles`, `renderHierarchySection`, `renderSubSection`, `renderSection` — hierarchy rendering
- `inv-bom-mode.js`: `buildNearMissMap`, `renderBomComparison`, `renderRemainingInventory`, `renderRemainingNormalSections` — BOM-mode rendering
- `inv-mutations.js`: `createReverseLink`, `confirmMatch`, `unconfirmMatch`, `confirmAltMatch`, `inferPartType`, `autoCreateGroupAndOpenFlyout`, `handleBomTableClick` — BOM click handling
- `inv-row-build.js`: `createPartRow` — single inventory row DOM builder
- `inv-sort-group.js`: `nextScope`, `sortPartsBy`, `groupByVendor` — pure sort/group helpers
- `inv-groups-view.js`: `renderGroupedView`, `renderFilterRow`, `applyGroupFilters` — groups mode
- `inventory-logic.js`: `groupBySection`, `filterByQuery`, `inferDistributor`, `countByDistributor`, `filterByDistributor`, `filterByVendor`, `computeMatchedInvKeys`, `sortBomRows`, `buildRowMap`, `groupPartsByGeneric`, `computeFilterDimensions`, `filterMembersByChips`, `bomRowDisplayData`, `BOM_STATUS_SORT_ORDER` — pure logic
- `inventory-renderer.js`: `renderSectionHeader`, `renderSubSectionHeader`, `renderPartRowHtml`, `createBomRowElement`, `renderAltRows`, `renderMemberRows`, `renderFilterBarHtml`, `renderBomTableHeader`, `renderInvColHeader`, `countStatuses` — pure HTML builders
- `favicon-stack.js`: `renderFanStack`, `buildHoverFlyout` — vendor favicon fan-stack
- `vendor-flyout.js`: `openVendorPopover`, `closeVendorPopover` — vendor management popover

## Imports from

- `../event-bus.js`, `../store.js`, `../api.js`, `../ui-helpers.js`, `../part-keys.js`, `../inventory-modals.js`, `../undo-redo.js`
- `../group-flyout/flyout-panel.js` — openFlyout (also via dynamic import)
- `../import/mfg-direct/mfg-direct-panel.js` — editPO (dynamic import)

## Internal layout

- `inventory-panel.js` — thin init + render coordinator; `inv-events.js` — event wiring
- `inv-state.js` — mutable state singleton; `inv-sort-group.js` — sort/group (pure)
- `inv-render.js` — section hierarchy; `inv-bom-mode.js` — BOM comparison view
- `inv-mutations.js` — match mutations + click handler; `inv-row-build.js` — row builder
- `inv-groups-view.js` — groups mode; `inventory-logic.js` — pure logic
- `inventory-renderer.js` — pure HTML; `favicon-stack.js` — favicons; `vendor-flyout.js` — vendor popover
