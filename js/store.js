/* store.js — Global application state */

import { EventBus, Events } from './event-bus.js';
import { SECTION_ORDER } from './constants.js';
import { api, AppLog } from './api.js';

export const App = {
  // Data (owned by store, set via API)
  inventory: [],

  // BOM state (owned by bom-panel)
  bomResults: null,
  bomFileName: "",
  bomHeaders: [],
  bomCols: {},
  bomDirty: false,

  // Linking state (central mutation API)
  links: {
    manualLinks: [],
    confirmedMatches: [],
    linkingMode: false,
    linkingInvItem: null,
    linkingBomRow: null,

    addManualLink(bk, ipk) {
      this.manualLinks.push({ bomKey: bk, invPartKey: ipk });
      EventBus.emit(Events.LINKS_CHANGED);
    },
    confirmMatch(bk, ipk) {
      this.confirmedMatches = this.confirmedMatches.filter(c => c.bomKey !== bk);
      this.confirmedMatches.push({ bomKey: bk, invPartKey: ipk });
      EventBus.emit(Events.CONFIRMED_CHANGED);
    },
    unconfirmMatch(bk) {
      this.confirmedMatches = this.confirmedMatches.filter(c => c.bomKey !== bk);
      EventBus.emit(Events.CONFIRMED_CHANGED);
    },
    setLinkingMode(active, invItem) {
      this.linkingMode = active;
      this.linkingInvItem = active ? invItem : null;
      this.linkingBomRow = null;
      EventBus.emit(Events.LINKING_MODE, { active, invItem: this.linkingInvItem });
    },
    setReverseLinkingMode(active, bomRow) {
      this.linkingMode = active;
      this.linkingBomRow = active ? bomRow : null;
      this.linkingInvItem = null;
      EventBus.emit(Events.LINKING_MODE, { active, bomRow: this.linkingBomRow });
    },
    loadFromSaved(savedLinks) {
      if (Array.isArray(savedLinks)) {
        this.manualLinks = savedLinks;
        this.confirmedMatches = [];
      } else if (savedLinks && typeof savedLinks === "object") {
        this.manualLinks = Array.isArray(savedLinks.manualLinks) ? savedLinks.manualLinks : [];
        this.confirmedMatches = Array.isArray(savedLinks.confirmedMatches) ? savedLinks.confirmedMatches : [];
      } else {
        this.manualLinks = [];
        this.confirmedMatches = [];
      }
      this.linkingMode = false;
      this.linkingInvItem = null;
      this.linkingBomRow = null;
    },
    clearAll() {
      this.manualLinks = [];
      this.confirmedMatches = [];
      this.linkingMode = false;
      this.linkingInvItem = null;
      this.linkingBomRow = null;
    },
    hasLinks() {
      return this.manualLinks.length > 0 || this.confirmedMatches.length > 0;
    },
  },

  // Configuration (read-only, from data/constants.json)
  SECTION_ORDER,
  SECTION_HIERARCHY: [],   // [{name, children: [...] | null}]
  FLAT_SECTIONS: [],       // flat list of all section strings

  // Preferences (owned by store)
  preferences: { thresholds: {} },
};

// Parse mixed SECTION_ORDER into hierarchy + flat list
(function parseSectionOrder() {
  const raw = App.SECTION_ORDER;
  const hierarchy = [];
  const flat = [];
  for (let i = 0; i < raw.length; i++) {
    const entry = raw[i];
    if (typeof entry === "string") {
      hierarchy.push({ name: entry, children: null });
      flat.push(entry);
    } else {
      hierarchy.push({ name: entry.name, children: entry.children });
      flat.push(entry.name);
      for (let j = 0; j < entry.children.length; j++) {
        flat.push(entry.name + " > " + entry.children[j]);
      }
    }
  }
  App.SECTION_HIERARCHY = hierarchy;
  App.FLAT_SECTIONS = flat;
})();

export function snapshotLinks() {
  return {
    manualLinks: JSON.parse(JSON.stringify(App.links.manualLinks)),
    confirmedMatches: JSON.parse(JSON.stringify(App.links.confirmedMatches)),
  };
}

// ── Preferences ──

export async function loadPreferences() {
  const stored = await api("load_preferences");
  if (stored && typeof stored === "object") {
    if (stored.thresholds) App.preferences.thresholds = stored.thresholds;
    if (stored.lastBomDir) App.preferences.lastBomDir = stored.lastBomDir;
    if (stored.lastImportDir) App.preferences.lastImportDir = stored.lastImportDir;
    if (stored.lastBomFile) App.preferences.lastBomFile = stored.lastBomFile;
  }
}

export async function savePreferences() {
  await api("save_preferences", JSON.stringify(App.preferences));
}

export function getThreshold(section) {
  if (section in App.preferences.thresholds) return App.preferences.thresholds[section];
  // Fallback: compound "Parent > Sub" → try parent threshold
  const sep = section.indexOf(" > ");
  if (sep !== -1) {
    const parent = section.substring(0, sep);
    if (parent in App.preferences.thresholds) return App.preferences.thresholds[parent];
  }
  return 50;
}

export function setThreshold(section, value) {
  App.preferences.thresholds[section] = value;
  savePreferences();
  EventBus.emit(Events.PREFS_CHANGED);
}

// ── Inventory loading ──

export function updateInventoryHeader() {
  document.getElementById("inv-count").textContent = App.inventory.length + " parts";
  const total = App.inventory.reduce((sum, item) => sum + item.qty * (item.unit_price || 0), 0);
  document.getElementById("inv-total-value").textContent = "$" + total.toFixed(2);
}

export async function loadInventory() {
  const fresh = await api("rebuild_inventory");
  if (!fresh) return;
  App.inventory = fresh;
  updateInventoryHeader();
  EventBus.emit(Events.INVENTORY_LOADED, App.inventory);
  AppLog.info("Loaded inventory: " + App.inventory.length + " parts");
}

export function onInventoryUpdated(freshInventory) {
  App.inventory = freshInventory;
  updateInventoryHeader();
  EventBus.emit(Events.INVENTORY_UPDATED, App.inventory);
}
