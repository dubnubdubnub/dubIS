/* store.js --- Centralized state management with getter/setter pairs.
   Panels import `store` (read-only getters) and setter functions directly.
   `App` is a plain read-only object kept for Python evaluate_js + App.links. */

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
let preferences = { thresholds: {} };
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

// ── Read-only store (new API — modules can migrate to this) ──

export const store = {
  get inventory() { return inventory; },
  get bomResults() { return bomResults; },
  get bomFileName() { return bomFileName; },
  get bomHeaders() { return bomHeaders; },
  get bomCols() { return bomCols; },
  get bomDirty() { return bomDirty; },
  get preferences() { return preferences; },
  get links() {
    return {
      get manualLinks() { return manualLinks; },
      get confirmedMatches() { return confirmedMatches; },
      get linkingMode() { return linkingActive; },
      get linkingInvItem() { return linkingInvItem; },
      get linkingBomRow() { return linkingBomRow; },
    };
  },
  SECTION_ORDER,
  SECTION_HIERARCHY,
  FLAT_SECTIONS,
};

// ── Setters (new API) ─────────────────────────────────────

export function setInventory(items) { inventory = items; }
// NOTE: setInventory does NOT emit events --- callers (loadInventory, onInventoryUpdated) handle that

export function setBomResults(results) { bomResults = results; }

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

// ── App object (read-only, for Python evaluate_js + window.App) ──
//
// Panels use `store` getters and setter functions directly.
// App is kept for Python interop (evaluate_js) and `App.links`.

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

export const App = {
  get inventory() { return inventory; },
  get bomResults() { return bomResults; },
  get bomFileName() { return bomFileName; },
  get bomHeaders() { return bomHeaders; },
  get bomCols() { return bomCols; },
  get bomDirty() { return bomDirty; },
  get preferences() { return preferences; },
  get genericParts() { return genericParts; },
  set genericParts(v) { genericParts = v; },
  links: _linksProxy,
  SECTION_ORDER,
  SECTION_HIERARCHY,
  FLAT_SECTIONS,
};

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
