/* store.js --- Centralized state management with getter/setter pairs.
   Panels import `store` (read-only getters) and setter functions directly.
   `window.store` is exposed in app-init.js for E2E tests and Python evaluate_js. */

import { EventBus, Events } from './event-bus.js';
import { SECTION_ORDER } from './constants.js';
import { api, AppLog } from './api.js';

// ── Private state slices ──────────────────────────────────
let inventory = [];
let bomResults = null;
let bomFileName = "";
let bomHeaders = [];
let bomCols = {};
let bomDirty = false;
let bomFootprintNearMisses = [];
let preferences = {
  thresholds: {},
  inventory_view: { group_level: 0, sort_column: null, sort_scope: null, vendor_group_scope: null },
};
let manualLinks = [];
let confirmedMatches = [];
let genericParts = [];
let linkingActive = false;
let linkingInvItem = null;
let linkingBomRow = null;

// ── Derived constants (computed once from SECTION_ORDER) ──

function parseSectionOrder(raw) {
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
  return { hierarchy, flat };
}

const _parsed = parseSectionOrder(SECTION_ORDER);
const SECTION_HIERARCHY = _parsed.hierarchy;
const FLAT_SECTIONS = _parsed.flat;

// ── Links proxy (store.links returns this object) ──

const _linksProxy = {
  get manualLinks() { return manualLinks; },
  set manualLinks(v) { manualLinks = v; },
  get confirmedMatches() { return confirmedMatches; },
  set confirmedMatches(v) { confirmedMatches = v; },
  get linkingMode() { return linkingActive; },
  get linkingInvItem() { return linkingInvItem; },
  get linkingBomRow() { return linkingBomRow; },

  addManualLink(bk, ipk) { addManualLink(bk, ipk); },
  confirmMatch(bk, ipk) { confirmMatch(bk, ipk); },
  unconfirmMatch(bk) { unconfirmMatch(bk); },
  setLinkingMode(active, invItem) { setLinkingMode(active, invItem); },
  setReverseLinkingMode(active, bomRow) { setReverseLinkingMode(active, bomRow); },
  loadFromSaved(savedLinks) { loadLinks(savedLinks); },
  clearAll() { clearLinks(); },
  hasLinks() { return hasLinks(); },
};

// ── Store (single public API for all state) ──

export const store = {
  get inventory() { return inventory; },
  get bomResults() { return bomResults; },
  set bomResults(v) { bomResults = v; },
  get bomFileName() { return bomFileName; },
  set bomFileName(v) { bomFileName = v; },
  get bomHeaders() { return bomHeaders; },
  set bomHeaders(v) { bomHeaders = v; },
  get bomCols() { return bomCols; },
  set bomCols(v) { bomCols = v; },
  get bomDirty() { return bomDirty; },
  get bomFootprintNearMisses() { return bomFootprintNearMisses; },
  get preferences() { return preferences; },
  get genericParts() { return genericParts; },
  set genericParts(v) { genericParts = v; },
  get links() { return _linksProxy; },
  SECTION_ORDER,
  SECTION_HIERARCHY,
  FLAT_SECTIONS,
};

// ── Setters (new API) ─────────────────────────────────────

export function setInventory(items) { inventory = items; }
// NOTE: setInventory does NOT emit events --- callers (loadInventory, onInventoryUpdated) handle that

export function setBomResults(results) { bomResults = results; }

export function setBomFootprintNearMisses(nm) { bomFootprintNearMisses = nm || []; }

export function setBomMeta({ fileName, headers, cols } = {}) {
  if (fileName !== undefined) bomFileName = fileName;
  if (headers !== undefined) bomHeaders = headers;
  if (cols !== undefined) bomCols = cols;
}

export function setBomDirty(dirty) { bomDirty = dirty; }

export function setPreferences(prefs) { preferences = { ...preferences, ...prefs }; }

// ── Link setters ──────────────────────────────────────────

export function addManualLink(bk, ipk) {
  manualLinks.push({ bomKey: bk, invPartKey: ipk });
  EventBus.emit(Events.LINKS_CHANGED);
}

export function confirmMatch(bk, ipk) {
  confirmedMatches = confirmedMatches.filter(c => c.bomKey !== bk);
  confirmedMatches.push({ bomKey: bk, invPartKey: ipk });
  EventBus.emit(Events.CONFIRMED_CHANGED);
}

export function unconfirmMatch(bk) {
  confirmedMatches = confirmedMatches.filter(c => c.bomKey !== bk);
  EventBus.emit(Events.CONFIRMED_CHANGED);
}

export function setLinkingMode(active, invItem) {
  linkingActive = active;
  linkingInvItem = active ? invItem : null;
  linkingBomRow = null;
  EventBus.emit(Events.LINKING_MODE, { active, invItem: linkingInvItem });
}

export function setReverseLinkingMode(active, bomRow) {
  linkingActive = active;
  linkingBomRow = active ? bomRow : null;
  linkingInvItem = null;
  EventBus.emit(Events.LINKING_MODE, { active, bomRow: linkingBomRow });
}

export function loadLinks(savedLinks) {
  if (Array.isArray(savedLinks)) {
    manualLinks = savedLinks;
    confirmedMatches = [];
  } else if (savedLinks && typeof savedLinks === "object") {
    manualLinks = Array.isArray(savedLinks.manualLinks) ? savedLinks.manualLinks : [];
    confirmedMatches = Array.isArray(savedLinks.confirmedMatches) ? savedLinks.confirmedMatches : [];
  } else {
    manualLinks = [];
    confirmedMatches = [];
  }
  linkingActive = false;
  linkingInvItem = null;
  linkingBomRow = null;
}

export function clearLinks() {
  manualLinks = [];
  confirmedMatches = [];
  linkingActive = false;
  linkingInvItem = null;
  linkingBomRow = null;
}

export function hasLinks() {
  return manualLinks.length > 0 || confirmedMatches.length > 0;
}

// ── snapshotLinks (existing API, unchanged behavior) ──────

export function snapshotLinks() {
  return {
    manualLinks: JSON.parse(JSON.stringify(manualLinks)),
    confirmedMatches: JSON.parse(JSON.stringify(confirmedMatches)),
  };
}

// ── Preferences ───────────────────────────────────────────

export async function loadPreferences() {
  const stored = await api("load_preferences");
  if (stored && typeof stored === "object") {
    if (stored.thresholds) preferences.thresholds = stored.thresholds;
    if (stored.lastBomDir) preferences.lastBomDir = stored.lastBomDir;
    if (stored.lastImportDir) preferences.lastImportDir = stored.lastImportDir;
    if (stored.lastBomFile) preferences.lastBomFile = stored.lastBomFile;
    if (stored.inventory_view && typeof stored.inventory_view === "object") {
      preferences.inventory_view = {
        group_level: Number.isInteger(stored.inventory_view.group_level) ? stored.inventory_view.group_level : 0,
        sort_column: stored.inventory_view.sort_column || null,
        sort_scope: stored.inventory_view.sort_scope || null,
        vendor_group_scope: stored.inventory_view.vendor_group_scope || null,
      };
    }
  }
}

export async function savePreferences() {
  await api("save_preferences", JSON.stringify(preferences));
}

export function getThreshold(section) {
  if (section in preferences.thresholds) return preferences.thresholds[section];
  // Fallback: compound "Parent > Sub" -> try parent threshold
  const sep = section.indexOf(" > ");
  if (sep !== -1) {
    const parent = section.substring(0, sep);
    if (parent in preferences.thresholds) return preferences.thresholds[parent];
  }
  return 50;
}

export function setThreshold(section, value) {
  preferences.thresholds[section] = value;
  savePreferences();
  EventBus.emit(Events.PREFS_CHANGED);
}

export function saveInventoryView(view) {
  preferences.inventory_view = {
    group_level: view.groupLevel,
    sort_column: view.sortColumn,
    sort_scope: view.sortScope,
    vendor_group_scope: view.vendorGroupScope,
  };
  savePreferences();
}

// ── Inventory loading ─────────────────────────────────────

export function updateInventoryHeader() {
  document.getElementById("inv-count").textContent = inventory.length + " parts";
  const total = inventory.reduce((sum, item) => sum + item.qty * (item.unit_price || 0), 0);
  document.getElementById("inv-total-value").textContent = "$" + total.toFixed(2);
}

export async function loadInventory() {
  const fresh = await api("rebuild_inventory");
  if (!fresh) return;
  inventory = fresh;
  updateInventoryHeader();
  EventBus.emit(Events.INVENTORY_LOADED, inventory);
  AppLog.info("Loaded inventory: " + inventory.length + " parts");
  // Load generic parts for BOM matching
  try {
    const gps = await api("list_generic_parts");
    genericParts = Array.isArray(gps) ? gps : [];
    EventBus.emit(Events.GENERIC_PARTS_LOADED, genericParts);
    if (genericParts.length > 0) {
      AppLog.info("Loaded " + genericParts.length + " generic parts");
    }
  } catch (e) {
    AppLog.warn("Failed to load generic parts: " + e);
    genericParts = [];
  }
}

export function onInventoryUpdated(freshInventory) {
  inventory = freshInventory;
  updateInventoryHeader();
  EventBus.emit(Events.INVENTORY_UPDATED, inventory);
}
