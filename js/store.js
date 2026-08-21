// @ts-check
/* store.js --- Centralized state management with getter/setter pairs.
   Panels import `store` (read-only getters) and setter functions directly.
   `window.store` is exposed in app-init.js for E2E tests and Python evaluate_js. */

import { EventBus, Events } from './event-bus.js';
import { signal } from './signals.js';
import { SECTION_ORDER } from './constants.js';
import { api, AppLog } from './api.js';
import { onEvent } from './sse.js';
import { formatMoney } from './ui-helpers.js';

// Debounce window for the SSE-driven inventory refresh (trailing debounce:
// the timer resets on every event and fires once quiet). 250ms per Task 3
// of docs/plans/2026-07-16-phase1b-frontend-port-plan.md.
const INVENTORY_UPDATED_DEBOUNCE_MS = 250;

// ── Shortcut preferences defaults ──────────────────────────

export const SHORTCUT_DEFAULTS = Object.freeze({
  redo: 'both',               // 'both' | 'ctrl-y' | 'ctrl-shift-z'
  enterSubmitsModals: true,
  vimNav: false,
});

// ── Behavior preferences defaults ──────────────────────────
/** @type {Object} */
export const BEHAVIOR_DEFAULTS = Object.freeze({
  autoCopySelection: false,   // auto-copy highlighted/selected text to clipboard
  // Spend above which a whole reel stops being a convenience worth paying for.
  // A DEFAULT RULE, not a constant and not a budget: it decides which reel the
  // cart's reel preset prefers and never hides one that exists, so a $90 reel
  // still appears (flagged) when nothing cheaper is carried on a reel.
  reelCeiling: 80,
});

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
  shortcuts: { ...SHORTCUT_DEFAULTS },
  behavior: { ...BEHAVIOR_DEFAULTS },
  saved_views: [],
};

// ── Signals ───────────────────────────────────────────────

/**
 * Signal wrapping `preferences`. Listeners call `.get()` inside an effect;
 * writers call `.set(preferences)` after mutating the object.
 * Signal holding the preferences object (replaced the old PREFS_CHANGED EventBus event).
 */
export const preferencesSignal = signal(preferences);
let manualLinks = [];
let confirmedMatches = [];
let genericParts = [];
let vendors = [];
let purchaseOrders = [];
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
  restoreLinks(data) { restoreLinks(data); },
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
  get vendors() { return vendors; },
  set vendors(v) { vendors = v; },
  get purchaseOrders() { return purchaseOrders; },
  set purchaseOrders(v) { purchaseOrders = v; },
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

/**
 * @param {{ fileName?: string, headers?: string[], cols?: Record<string,string> }} [opts]
 */
export function setBomMeta({ fileName, headers, cols } = {}) {
  if (fileName !== undefined) bomFileName = fileName;
  if (headers !== undefined) bomHeaders = headers;
  if (cols !== undefined) bomCols = cols;
}

export function setBomDirty(dirty) { bomDirty = dirty; }

/** Mark the BOM as having unsaved changes and tell Python (which owns the
 * close-confirm modal via api._bom_dirty). Called by the link/confirm mutations
 * below — all of which are user actions (initial load uses loadLinks(), and
 * undo/redo assigns the arrays directly, so neither routes through here). */
function markBomDirty() {
  bomDirty = true;
  api('set_bom_dirty', true);
}

export function setPreferences(prefs) { preferences = { ...preferences, ...prefs }; }

/* Did a re-fetch actually bring back different data?
   Both lists arrive straight from /v1 as parsed JSON built from the same
   server-side row order and key order, so a string compare is a sound change
   test here — and far cheaper than a structural walk over every PO. */
function sameData(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

/* VENDORS_CHANGED / PO_CHANGED are CHANGE events, not refresh notifications:
   they must not fire when a re-fetch returns byte-identical data.
   loadVendorsAndPOs() runs after EVERY inventory mutation (onInventoryUpdated),
   including ones that cannot touch vendors or POs at all — e.g. the
   record_fetched_prices write a *hover* tooltip performs, which publishes
   `inventory.updated` over SSE. Emitting unconditionally turned that passive
   hover into a PO_CHANGED, which panel-collapse.js reads as "the user changed
   POs" and answers by force-reopening the collapsed Purchase Import panel. */
export function setVendors(list) {
  const next = list || [];
  const changed = !sameData(vendors, next);
  vendors = next;
  if (changed) EventBus.emit(Events.VENDORS_CHANGED, vendors);
}

export function setPurchaseOrders(list) {
  const next = list || [];
  const changed = !sameData(purchaseOrders, next);
  purchaseOrders = next;
  if (changed) EventBus.emit(Events.PO_CHANGED, purchaseOrders);
}

export async function loadVendorsAndPOs() {
  const [vs, pos] = await Promise.all([
    api('list_vendors'),
    api('list_purchase_orders'),
  ]);
  setVendors(vs);
  setPurchaseOrders(pos);
}

// ── Link setters ──────────────────────────────────────────

export function addManualLink(bk, ipk) {
  manualLinks.push({ bomKey: bk, invPartKey: ipk });
  markBomDirty();
  EventBus.emit(Events.LINKS_CHANGED);
}

export function confirmMatch(bk, ipk) {
  confirmedMatches = confirmedMatches.filter(c => c.bomKey !== bk);
  confirmedMatches.push({ bomKey: bk, invPartKey: ipk });
  markBomDirty();
  EventBus.emit(Events.CONFIRMED_CHANGED);
}

export function unconfirmMatch(bk) {
  confirmedMatches = confirmedMatches.filter(c => c.bomKey !== bk);
  markBomDirty();
  EventBus.emit(Events.CONFIRMED_CHANGED);
}

/** Restore links + confirms from an undo/redo snapshot. This is a user action
 * that changes persisted BOM state, so it marks the BOM dirty (unlike
 * loadLinks(), which loads a freshly-saved BOM). */
export function restoreLinks({ manualLinks: ml, confirmedMatches: cm }) {
  manualLinks = Array.isArray(ml) ? ml : [];
  confirmedMatches = Array.isArray(cm) ? cm : [];
  markBomDirty();
  EventBus.emit(Events.LINKS_CHANGED);
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
    if (stored.shortcuts && typeof stored.shortcuts === "object") {
      preferences.shortcuts = normalizeShortcuts(stored.shortcuts);
    }
    if (stored.behavior && typeof stored.behavior === "object") {
      preferences.behavior = normalizeBehavior(stored.behavior);
    }
    // Raw pass-through: both are validated by their own owning module —
    // ui_zoom by normalizePersistedZoom (js/ui-zoom-logic.js) and
    // panels_collapsed by normalizeCollapsed (js/panel-collapse-logic.js) —
    // which repair malformed values rather than rejecting them here.
    if (Object.prototype.hasOwnProperty.call(stored, 'ui_zoom')) {
      preferences.ui_zoom = stored.ui_zoom;
    }
    if (Object.prototype.hasOwnProperty.call(stored, 'panels_collapsed')) {
      preferences.panels_collapsed = stored.panels_collapsed;
    }
    if (Object.prototype.hasOwnProperty.call(stored, 'saved_views')) {
      if (Array.isArray(stored.saved_views)) {
        // Filter out malformed entries (must have string id and name)
        preferences.saved_views = stored.saved_views.filter(function (entry) {
          if (!entry || typeof entry !== "object") {
            AppLog.warn("load_preferences: ignoring non-object saved_view entry");
            return false;
          }
          if (!entry.id || typeof entry.id !== "string") {
            AppLog.warn("load_preferences: ignoring saved_view entry with missing/invalid id");
            return false;
          }
          if (!entry.name || typeof entry.name !== "string") {
            AppLog.warn("load_preferences: ignoring saved_view entry \"" + entry.id + "\" with missing/invalid name");
            return false;
          }
          return true;
        });
      } else {
        AppLog.warn("load_preferences: saved_views is not an array — ignoring");
      }
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
  preferencesSignal.set(preferences);
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
  const total = inventory.reduce((sum, /** @type {import('./types.js').InventoryItem} */ item) => sum + item.qty * (item.unit_price || 0), 0);
  document.getElementById("inv-total-value").textContent = formatMoney(total);
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
    if (genericParts.length > 0) {
      AppLog.info("Loaded " + genericParts.length + " generic parts");
    }
  } catch (e) {
    AppLog.warn("Failed to load generic parts: " + e);
    genericParts = [];
  }
  // Load vendors and purchase orders
  try {
    await loadVendorsAndPOs();
  } catch (e) {
    AppLog.warn("Failed to load vendors/POs: " + e);
  }
  // Surface migration / duplicate / inferred-only warnings
  try {
    const w = await api('get_warnings');
    if (w.migration && (w.migration.inferred_count || w.migration.unknown_count)) {
      const m = w.migration;
      if (m.inferred_count) AppLog.warn(`Migration: created ${m.inferred_count} inferred vendor(s) from existing manufacturers`);
      if (m.unknown_count) AppLog.warn(`Migration: ${m.unknown_count} parts have no manufacturer — assigned to ❓ Unknown`);
    }
    if (w.inferred_only > 0) {
      AppLog.warn(`${w.inferred_only} vendor(s) lack URLs — add URLs to enable favicons`);
    }
    (w.duplicates || []).forEach(d => {
      AppLog.warn(`Vendor "${d.src.name}" and "${d.dst.name}" look similar — merge?`);
    });
  } catch (e) {
    AppLog.warn("Failed to load warnings: " + e);
  }
}

export function onInventoryUpdated(freshInventory) {
  inventory = freshInventory;
  updateInventoryHeader();
  EventBus.emit(Events.INVENTORY_UPDATED, inventory);
  // Refresh vendors and purchase orders after any inventory mutation
  loadVendorsAndPOs().catch(e => AppLog.warn("Failed to refresh vendors/POs: " + e));
}

/**
 * SSE-driven counterpart to `loadInventory()`: re-fetches inventory and feeds
 * it through the normal update path, but skips the one-time load side effects
 * (splash dismissal, generic-parts/vendor bootstrap, migration warnings) —
 * those already ran during the initial `loadInventory()` and re-running them
 * on every push would be redundant and noisy.
 */
export async function loadInventoryQuiet() {
  const fresh = await api("rebuild_inventory");
  if (!fresh) {
    AppLog.warn("inventory refresh failed");
    return;
  }
  onInventoryUpdated(fresh);
}

// ── SSE-driven refresh (Task 10 flip) ──────────────────────
// SSE is the single re-render source, but mutation call sites also call
// this directly (belt-and-braces): under real usage the mutation's own
// `inventory.updated` SSE push and the direct call collapse into ONE
// refresh via this shared debounce (both share `_inventoryUpdatedTimer` /
// `_inventoryUpdatedResolvers`, so a mutation call site's await and the SSE
// push it triggers resolve together off a single `loadInventoryQuiet()`);
// under route-mocked tests (no live SSE stream) the direct call is what
// actually drives the post-mutation refetch. Call sites that need the
// freshly-updated item read it from `store.inventory` after the returned
// promise resolves — mutation responses no longer carry inventory data.
// `onEvent` is safe to call before `connectEvents()` opens the connection —
// see js/sse.js.
let _inventoryUpdatedTimer = null;
let _inventoryUpdatedResolvers = [];

function fireInventoryRefresh() {
  _inventoryUpdatedTimer = null;
  const resolvers = _inventoryUpdatedResolvers;
  _inventoryUpdatedResolvers = [];
  loadInventoryQuiet().then(
    () => resolvers.forEach((r) => r.resolve()),
    (e) => resolvers.forEach((r) => r.reject(e)),
  );
}

/** @returns {Promise<void>} resolves once the (possibly-shared) debounced refresh completes. */
export function scheduleInventoryRefresh() {
  clearTimeout(_inventoryUpdatedTimer);
  const p = new Promise((resolve, reject) => {
    _inventoryUpdatedResolvers.push({ resolve, reject });
  });
  _inventoryUpdatedTimer = setTimeout(fireInventoryRefresh, INVENTORY_UPDATED_DEBOUNCE_MS);
  return p;
}

onEvent('inventory.updated', () => {
  scheduleInventoryRefresh().catch(e => AppLog.warn("inventory refresh failed: " + e));
});

// ── Shortcut preferences ──────────────────────────────────

function normalizeShortcuts(s) {
  const redo = ['both', 'ctrl-y', 'ctrl-shift-z'].includes(s.redo) ? s.redo : SHORTCUT_DEFAULTS.redo;
  return {
    redo,
    enterSubmitsModals: typeof s.enterSubmitsModals === 'boolean' ? s.enterSubmitsModals : SHORTCUT_DEFAULTS.enterSubmitsModals,
    vimNav: typeof s.vimNav === 'boolean' ? s.vimNav : SHORTCUT_DEFAULTS.vimNav,
  };
}

export function getShortcutPrefs() {
  return normalizeShortcuts(preferences.shortcuts || {});
}

export function setShortcutPrefs(partial) {
  preferences.shortcuts = normalizeShortcuts({ ...getShortcutPrefs(), ...partial });
  savePreferences();
  preferencesSignal.set(preferences);
}

/**
 * Coerce a stored behavior-preferences object to the known keys.
 *
 * This whitelist is applied on BOTH read and write on purpose — an unknown key
 * is dropped rather than persisted, so preferences.json cannot accumulate
 * fields nothing reads. The corollary is the trap: a new preference that is
 * not added HERE looks like it saved and comes back as the default, in both
 * directions and without an error.
 * @param {any} raw
 * @returns {{autoCopySelection: boolean, reelCeiling: number}}
 */
function normalizeBehavior(raw) {
  const b = raw || {};
  const ceiling = Number(b.reelCeiling);
  return {
    autoCopySelection: !!b.autoCopySelection,
    // 0 and negatives are not a ceiling of zero — they would reject every reel
    // there is — so they fall back to the shipped default.
    reelCeiling: Number.isFinite(ceiling) && ceiling > 0
      ? ceiling : BEHAVIOR_DEFAULTS.reelCeiling,
  };
}

export function getBehaviorPrefs() {
  return normalizeBehavior(preferences.behavior || {});
}

export function setBehaviorPrefs(partial) {
  preferences.behavior = normalizeBehavior({ ...getBehaviorPrefs(), ...partial });
  savePreferences();
  preferencesSignal.set(preferences);
}
