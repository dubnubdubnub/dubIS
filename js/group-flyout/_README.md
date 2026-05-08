# group-flyout — Generic-parts flyout panel: tag filtering, member list, saved searches

## Owns

Opens and manages floating flyout panels anchored to inventory rows for editing
generic parts groups. Owns flyout instance state (tags, search text, frozen state,
saved searches), drag-and-drop wiring between flyout and inventory rows. Does NOT
own generic-parts data (owned by `store.js`).

## Emits

- `FLYOUT_OPENED` — `flyout-panel.js:openFlyout` when a flyout is first created
- `FLYOUT_CLOSED` — `flyout-panel.js:closeFlyout` when a flyout is closed
- `FLYOUT_ACTIVE_CHANGED` — `flyout-panel.js:activateFlyout` when focus moves between flyouts
- `FLYOUT_SEARCH_CHANGED` — `flyout-events.js:wireEvents` when user types in flyout search

## Public exports

- `flyout-panel.js`: `activateFlyout`, `closeFlyout`, `rerenderFlyout`, `openFlyout`, `init` — public API for opening/closing/rendering flyouts
- `flyout-state.js`: `flyouts`, `activeFlyoutId`, `setActiveFlyoutId` — shared flyout instance map
- `flyout-logic.js`: `generateTags`, `detectConflicts`, `filterMembers`, `generateDefaultSearchName` — pure tag/filter logic
- `flyout-renderer.js`: `renderFlyout` — pure HTML renderer
- `flyout-drag.js`: `setRerenderFlyout`, `wireDrag`, `wireInventoryDrag`, `unwireInventoryDrag` — drag-and-drop wiring
- `flyout-events.js`: `setPanelFunctions`, `wireEvents`, `unwireEvents` — DOM event wiring

## Imports from

- `../event-bus.js`, `../store.js`, `../api.js`
- `../ui-helpers.js` — escHtml

## Internal layout

- `flyout-panel.js` — public API: open, close, activate, rerender
- `flyout-state.js` — flyout instance map and active-flyout ID
- `flyout-logic.js` — pure tag/filter/search logic
- `flyout-renderer.js` — pure HTML renderer
- `flyout-events.js` — DOM event wiring (search, tag chips, saved searches)
- `flyout-drag.js` — drag-and-drop between flyout and inventory rows
