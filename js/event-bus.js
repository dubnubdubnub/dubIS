/* event-bus.js — Central event system for cross-panel communication */

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
