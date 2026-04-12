// @ts-check
/* event-bus.js — Central event system for cross-panel communication */

/**
 * Event payloads (what `data` contains when the event fires):
 *
 * INVENTORY_LOADED:     InventoryItem[]          — full inventory array
 * INVENTORY_UPDATED:    InventoryItem[]          — updated inventory array
 * BOM_LOADED:           {rows, fileName, multiplier} — computed BOM rows + metadata
 * BOM_CLEARED:          (none)
 * PREFS_CHANGED:        (none)                   — listeners re-read store.preferences
 * CONFIRMED_CHANGED:    (none)                   — listeners re-read store.links
 * LINKING_MODE:         (none)                   — listeners re-read store.links.linkingMode etc.
 * LINKS_CHANGED:        (none)                   — listeners re-read store.links
 * SAVE_AND_CLOSE:       (none)
 * GENERIC_PARTS_LOADED: GenericPart[]            — full generic parts array
 */
export const Events = Object.freeze({
  INVENTORY_LOADED:  "inventory-loaded",
  INVENTORY_UPDATED: "inventory-updated",
  BOM_LOADED:        "bom-loaded",
  BOM_CLEARED:       "bom-cleared",
  PREFS_CHANGED:     "preferences-changed",
  CONFIRMED_CHANGED: "confirmed-match-changed",
  LINKING_MODE:      "linking-mode",
  LINKS_CHANGED:     "links-changed",
  SAVE_AND_CLOSE:    "save-and-close",
  GENERIC_PARTS_LOADED: "generic-parts-loaded",
  FLYOUT_OPENED:          "flyout-opened",
  FLYOUT_CLOSED:          "flyout-closed",
  FLYOUT_ACTIVE_CHANGED:  "flyout-active-changed",
  FLYOUT_SEARCH_CHANGED:  "flyout-search-changed",
});

export const EventBus = {
  _listeners: {},
  on(event, fn) {
    (this._listeners[event] = this._listeners[event] || []).push(fn);
  },
  off(event, fn) {
    const list = this._listeners[event];
    if (list) this._listeners[event] = list.filter(f => f !== fn);
  },
  emit(event, data) {
    (this._listeners[event] || []).forEach(fn => fn(data));
  }
};
