// @ts-check
/* store.js --- Centralized state management with getter/setter pairs.
   Panels import `store` (read-only getters) and setter functions directly.
   `window.store` is exposed in app-init.js for E2E tests and Python evaluate_js. */

import { EventBus, Events } from './event-bus.js';
import { signal, activeSourceSignal, sourceStatusSignal } from './signals.js';
import { SECTION_ORDER } from './constants.js';
import { api, apiEnvelope, AppLog, setSourceHeaderProvider } from './api.js';
import { onEvent } from './sse.js';
import { formatMoney } from './ui-helpers.js';
import {
  LOCAL_ID,
  normalizeServerUrl,
  normalizeServers,
  normalizeToken,
  tokenRejection,
  addServerEntry,
  updateServerEntry,
  removeServerEntry,
} from './servers-logic.js';
import {
  normalizeSources,
  sourcesFromRoster,
  normalizeTabs,
  seedTabs,
  reconcileTabs,
  resolveActiveTabId,
  allSourceIds,
  activeSourceValue,
  addTab,
  closeTab,
  moveTab,
  groupTabs,
  tabForSource,
  tabForSourceValue,
} from './server-tabs-logic.js';

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
  // The roster of servers offered in Preferences. Purely a list of candidates;
  // `server_url` below is what actually decides where we connect.
  servers: [],
  // "" = local mode (spawn our own server). See remote_mode.py.
  server_url: "",
  // The quick-switcher's open tabs: [{id, sources, name, view}]. A tab is a view
  // instance, not a server — same server twice is legal, and `sources` holds
  // several ids for a group — so this cannot be derived from `servers`.
  server_tabs: [],
  // Which of those tabs is in front.
  active_tab: '',
  // What the hub was last told to serve: "local", a source id, a comma-joined
  // set, or "merged". Derived from the active tab and written for two readers
  // that know nothing about tabs — the hub's own startup seed
  // (app_launch.seed_initial_active_source) and anyone reading preferences.json
  // by hand. `server_url` stays alongside it because it is the single answer to
  // "which ONE server" that remote_mode.py, app_restart.py and tools/dubis-cli
  // read, and a group has no honest answer for it. Empty means "nobody has ever
  // chosen", which is NOT the same as an explicit "local" — the difference is
  // what lets `server_url` seed a first-run window.
  active_source: '',
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
    // Carried through explicitly. savePreferences() posts the WHOLE in-memory
    // object, and this loader copies known keys only — so a key that is read
    // here is silently erased from preferences.json by the next save of any
    // unrelated preference. remote_mode.resolve_remote_base_url reads
    // server_url from that file, so dropping it would have quietly un-set the
    // remote server the moment the user touched a slider.
    preferences.server_url = normalizeServerUrl(stored.server_url);
    // Same carry-through reasoning as server_url above: unread keys are erased
    // by the next save of any unrelated preference, so the roster has to be
    // loaded here or adding a server would survive exactly until the user
    // touched a slider.
    //
    // Note what is NOT here, and never will be: a server's API token. The
    // roster is `{id, name, url}` and nothing else, because savePreferences()
    // posts this whole object into data/preferences.json — a hand-editable
    // file that every unrelated preference change rewrites. A source's
    // credential is used server-side, by the hub's outbound httpx client, and
    // is stored by server/token_store.py in its own file; this window only
    // ever writes one (PATCH /v1/sources/{id}, see setServerToken below) and
    // reads it back as the boolean `has_token`.
    preferences.servers = normalizeServers(stored.servers, function (msg) {
      AppLog.warn('load_preferences: ' + msg);
    });
    // And again for active_source — this is THE trap of this file. A new
    // preference key that is not read here looks like it saved and reads back
    // as its default, in both directions and without an error, because
    // savePreferences() posts the whole in-memory object and this loader is
    // the only thing that puts a stored value into it. Resolved against the
    // roster (not trusted raw) so an id naming a server that has since been
    // removed collapses to local rather than leaving the strip pointed at a
    // tab that no longer exists.
    // Three keys, all with the same trap: savePreferences() posts the WHOLE
    // in-memory object and this loader copies known keys only, so a key that is
    // NOT read here looks like it saved and reads back as its default, in both
    // directions and without an error. For these that would mean every launch
    // silently reopening the default tab set. Repair happens later, in
    // hydrateSourcesFromPreferences, which resolves them against the roster that
    // actually exists — a tab naming a deleted server must not survive.
    preferences.server_tabs = Array.isArray(stored.server_tabs) ? stored.server_tabs : [];
    preferences.active_tab = typeof stored.active_tab === 'string' ? stored.active_tab : '';
    preferences.active_source = typeof stored.active_source === 'string'
      ? stored.active_source : '';
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
    // Same raw pass-through: column_widths is validated by normalizeWidths
    // (js/col-resize-logic.js). Carrying it here is what lets col-resize.js
    // read the widths off the already-loaded preferences instead of issuing its
    // own load_preferences GET during panel init — that extra async round trip
    // reordered startup enough to push the roving grid's rAF re-arm past the
    // point the win11 E2E leg sampled it.
    if (Object.prototype.hasOwnProperty.call(stored, 'column_widths')) {
      preferences.column_widths = stored.column_widths;
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
  // Publish the roster + active source onto activeSourceSignal now that both
  // have been read. Without this the strip would render from the signal's
  // initial "local, no sources" until the first GET /v1/sources came back —
  // i.e. the saved tab would visibly flip to Local on every launch.
  hydrateSourcesFromPreferences();
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

/**
 * `GET /v1/parts`, keeping the part of the answer `api()` would throw away.
 *
 * A merged read answers `{inventory, sources}` — the rows, plus how the fetch
 * went at each server (`server/fanout.py`'s per-source status). `api()` unwraps
 * to `inventory` and the sibling is gone, which matters because the fan-out
 * DEGRADES rather than fails: a server that is asleep costs its stock and
 * nothing else, so a partial view is byte-for-byte a smaller complete one
 * unless something carries the status across. This is that something.
 *
 * A single-server read has no `sources` key and clears the signal, so the
 * "incomplete totals" marker cannot outlive the view that earned it.
 *
 * @returns {Promise<Array<Object>|null>} the inventory rows, or null on failure
 */
export async function fetchInventory() {
  const body = await apiEnvelope("rebuild_inventory");
  if (!body) return null;
  // Tolerant of a bare array: the client-shell bridge (and any older transport)
  // returns the rows with no envelope around them at all.
  if (Array.isArray(body)) {
    sourceStatusSignal.set([]);
    return body;
  }
  sourceStatusSignal.set(Array.isArray(body.sources) ? body.sources : []);
  return Array.isArray(body.inventory) ? body.inventory : null;
}

export async function loadInventory() {
  const fresh = await fetchInventory();
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
  const fresh = await fetchInventory();
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

// normalizeServerUrl now lives in js/servers-logic.js — the roster loader and
// the selection setter have to agree byte-for-byte on what a URL normalizes
// to, or a roster entry stops matching the `server_url` that selected it and
// the list shows nothing as selected.

/** @returns {string} the configured remote server URL, or "" for local mode. */
export function getServerUrl() {
  return normalizeServerUrl(preferences.server_url);
}

/**
 * Persist the remote server URL.
 *
 * No longer a next-launch-only setting: the window is always served by the
 * local hub, and other servers are data *sources* it fetches on our behalf, so
 * `switchActiveSource` below applies a change immediately. This key is still
 * written because three readers outside the frontend
 * (`remote_mode.resolve_remote_base_url`, `app_restart.relaunch_env` and
 * `tools/dubis-cli`) treat it as the single answer to "where does my data come
 * from", and it is what seeds the hub's active source at the next launch.
 * @param {string} url "" for local mode
 */
export function setServerUrl(url) {
  preferences.server_url = normalizeServerUrl(url);
  savePreferences();
  preferencesSignal.set(preferences);
  return preferences.server_url;
}

// ── Server roster ─────────────────────────────────────────
// The list of servers offered in Preferences. Selection is still `server_url`
// (see js/servers-logic.js for why), so these functions only ever maintain the
// menu — with one exception: removing the *selected* server has to move the
// selection too, or the app would keep connecting to an entry the user just
// deleted and the list would show it back as an "unlisted" row.

/** @returns {Array<{id: string, name: string, url: string}>} a copy of the roster. */
export function getServers() {
  return normalizeServers(preferences.servers).map((s) => ({ ...s }));
}

/** Ids are opaque and only need to be unique within one preferences file. */
function newServerId() {
  return 's' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
}

/**
 * Add a server to the roster. Does NOT select it.
 *
 * Stays synchronous — the roster is in-memory state and every caller renders
 * off it immediately. The optional `token` is therefore NOT sent here: it comes
 * back on `result.token` for the caller to hand to `setServerToken`, which is
 * async because it has to order two writes (see there). `addServerEntry`
 * guarantees the roster it returns carries no token key, so nothing a
 * credential was typed into can reach `preferences.servers`.
 * @param {string} name blank derives a name from the URL's host
 * @param {string} url
 * @param {string} [token] validated here, sent by the caller via setServerToken
 * @returns {{ok: boolean, reason?: string, entry?: {id: string, name: string, url: string}, token?: string}}
 */
export function addServer(name, url, token) {
  const result = addServerEntry(getServers(), { id: newServerId(), name, url, token });
  if (!result.ok) return result;
  preferences.servers = result.servers;
  savePreferences();
  preferencesSignal.set(preferences);
  // The strip is a view of the roster, so it has to move with the roster —
  // otherwise a server you just added is switchable only after a reload.
  syncTabsWithRoster();
  return { ok: true, entry: result.entry, token: result.token };
}

/**
 * Store (or clear) the API token the hub uses when it fetches from a source.
 *
 * The token never touches `preferences`, never reaches an AppLog line and never
 * reaches a toast — the only places it may appear are this function's argument
 * and the PATCH body. What comes back from the hub afterwards is the boolean
 * `has_token`, never the secret.
 *
 * The await ordering is load-bearing, not stylistic. `addServer` fires an
 * UN-awaited `savePreferences()`, and `PATCH /v1/sources/{id}` does a
 * read-modify-write of preferences.json server-side to find the source it is
 * patching. If the PATCH lands first, the roster entry does not exist on disk
 * yet and the route 404s — an added server that silently has no credential.
 * Awaiting our own save first is what makes the entry visible to the route.
 * @param {string} id a roster entry id
 * @param {string} token "" clears the stored token
 * @returns {Promise<{ok: boolean, reason?: string}>}
 */
export async function setServerToken(id, token) {
  // Before any network call: a token that cannot become an Authorization header
  // is a thing to tell the user about, not a request to make and have refused.
  const reason = tokenRejection(token);
  if (reason) return { ok: false, reason };
  await savePreferences();
  // `undefined` for name and url means "leave them alone" — JSON.stringify
  // drops undefined body fields, and the route reads a missing field as None.
  const result = await api('update_source', id, undefined, undefined, normalizeToken(token));
  if (result === undefined) {
    // How js/api.js reports a failed call. Deliberately says nothing about the
    // token's content.
    return { ok: false, reason: 'Could not save the token for that server' };
  }
  return { ok: true };
}

/**
 * Rename or re-point a roster entry. Re-pointing the *selected* entry moves
 * the selection with it, so editing a URL typo does not silently leave the app
 * pointed at the old address.
 * @param {string} id
 * @param {{name?: string, url?: string}} patch
 * @returns {{ok: boolean, reason?: string, entry?: {id: string, name: string, url: string}}}
 */
export function updateServer(id, patch) {
  const before = getServers().find((s) => s.id === id);
  const result = updateServerEntry(getServers(), id, patch);
  if (!result.ok) return result;
  preferences.servers = result.servers;
  if (before && before.url === getServerUrl() && result.entry.url !== before.url) {
    preferences.server_url = result.entry.url;
  }
  savePreferences();
  preferencesSignal.set(preferences);
  syncTabsWithRoster();
  return { ok: true, entry: result.entry };
}

/**
 * Remove a roster entry, falling the selection back to local if it was the
 * selected one.
 * @param {string} id
 * @returns {{removed: boolean, deselected: boolean}}
 */
export function removeServer(id) {
  const target = getServers().find((s) => s.id === id);
  if (!target) return { removed: false, deselected: false };
  preferences.servers = removeServerEntry(getServers(), id);
  const deselected = target.url === getServerUrl();
  if (deselected) preferences.server_url = '';
  savePreferences();
  preferencesSignal.set(preferences);
  // reconcileTabs (inside this) strips the removed source out of every tab and
  // closes any tab left with nothing — so the strip can never keep offering a
  // server that is no longer configured.
  syncTabsWithRoster();
  return { removed: true, deselected };
}

// ── Tabs ──────────────────────────────────────────────────
// The quick-switcher's tabs. A tab is a VIEW INSTANCE, not a server: it has its
// own id, its own set of sources (one, or several for a group) and its own
// inventory view state, which is why the same server can be open twice. All the
// decisions live in js/server-tabs-logic.js; this section owns persistence, the
// signal, and the one API call that changes what the hub serves.
//
// Everything cross-panel about it rides `activeSourceSignal` (js/signals.js):
// the tab strip and the Preferences roster are two views of one piece of state,
// and a panel that mounts after a switch has to see the switch, which an
// EventBus message cannot give it.

/** Ids are opaque and only need to be unique within one preferences file. */
function newTabId() {
  return 't' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
}

/** @returns {Array<import('./server-tabs-logic.js').Tab>} a copy of the open tabs. */
export function getTabs() {
  return activeSourceSignal.peek().tabs.map((t) => ({ ...t, sources: t.sources.slice() }));
}

/** @returns {string} the id of the tab in front. */
export function getActiveTabId() {
  return activeSourceSignal.peek().activeTabId;
}

/** @returns {import('./server-tabs-logic.js').Tab | undefined} */
export function getActiveTab() {
  const { tabs, activeTabId } = activeSourceSignal.peek();
  return tabs.find((t) => t.id === activeTabId);
}

/** @returns {Array<import('./signals.js').SourceEntry>} the hub's source roster. */
export function getSources() {
  return activeSourceSignal.peek().sources.map((s) => ({ ...s }));
}

/**
 * This window's `X-Dubis-Source` value: an id, a comma-joined set, or "merged".
 *
 * Empty until the tab signal is populated, and that emptiness is deliberate.
 * server/dispatch.py serves a header-less request from the hub's persisted
 * default, which is whatever window saved it last — so guessing "local" here
 * would be worse than sending nothing: it would pin a brand-new window to local
 * for its first requests instead of to the default the user launched with. The
 * only request that goes out before hydration is `load_preferences`, whose route
 * is local-only and never proxied.
 * @returns {string}
 */
export function getActiveSource() {
  const tab = getActiveTab();
  return tab ? activeSourceValue(tab, allSourceIds(getSources())) : '';
}

// Installed at import time, before any panel can issue a call. The hub keeps no
// mutable "current server", so every /v1 request has to name the one it wants —
// that is what makes two windows on two different servers independent rather
// than two views fighting over one saved default.
setSourceHeaderProvider(getActiveSource);

/**
 * The preferences roster, re-using whatever the hub has already told us.
 *
 * Adding or renaming a server must move the strip immediately, but it must not
 * throw away the reachability the hub reported for the servers that did not
 * change — a roster edit is not evidence that the other servers went away. A
 * re-pointed entry is the exception: its old probe result describes a different
 * machine, so it drops back to unknown.
 * @returns {Array<import('./signals.js').SourceEntry>}
 */
function rosterSyncedSources() {
  const known = new Map(activeSourceSignal.peek().sources.map((s) => [s.id, s]));
  return sourcesFromRoster(getServers()).map((s) => {
    const prev = known.get(s.id);
    return prev && prev.url === s.url ? { ...prev, name: s.name } : s;
  });
}

/**
 * Publish tabs + sources onto the signal and persist the tab set.
 *
 * `persist: false` is for a publish that only changed what the HUB told us
 * (reachability, say) — writing preferences on every poll would turn a
 * background probe into a disk write.
 *
 * `refresh: false` is for the startup seed alone: hydration is not a change the
 * user made, and `loadInventory()` is about to fetch with exactly this source
 * anyway, so refreshing here would cost every launch a duplicate round trip.
 * @param {Array<import('./server-tabs-logic.js').Tab>} tabs
 * @param {string} activeTabId
 * @param {Array<import('./signals.js').SourceEntry>} sources
 * @param {{persist?: boolean, refresh?: boolean}} [opts]
 */
function publishTabs(tabs, activeTabId, sources, opts) {
  const resolved = resolveActiveTabId(tabs, activeTabId);
  activeSourceSignal.set({ tabs, activeTabId: resolved, sources });
  if (!opts || opts.persist !== false) {
    // Before persistTabs(), which writes preferences: server_url is one of the
    // keys it saves. Here rather than in `switchToTab` because a switch is not
    // the only gesture that changes which single server is in front — closing
    // the front tab does too, and a stale server_url re-points tools/dubis-cli
    // and the next launch at a server this window is no longer showing.
    syncServerUrl();
    persistTabs();
  }
  syncShownSource({ refetch: !opts || opts.refresh !== false });
  return resolved;
}

/**
 * The `X-Dubis-Source` value the rows currently on screen were fetched with.
 *
 * Starts as the empty string, which is exactly what `getActiveSource()` answers
 * before the tabs are hydrated — so the startup seed is a no-op change and only
 * a real move costs a fetch.
 */
let shownSource = '';

/**
 * Refetch the inventory when — and only when — the source behind the grid moved.
 *
 * This lives here, in the one place that publishes what the window shows,
 * because "which source are the rows from" is the only honest trigger for a
 * refetch. It used to be `persistTabs()`'s return value — "did the saved default
 * change" — and that answered the wrong question for every gesture that
 * publishes twice. Closing the front tab publishes the surviving tab set (which
 * records the new default) and THEN calls `switchToTab`, whose own
 * `persistTabs()` truthfully reports "nothing changed" — so nothing refetched
 * and the grid went on showing the closed tab's server. Grouping had the same
 * shape, and so did a roster edit that deleted the server a tab was showing.
 *
 * Two tabs on the same server still cost no round trip: they serialize to the
 * same header value, so the switch between them is a re-render driven by the
 * view the caller applies.
 * @param {{refetch: boolean}} opts
 */
function syncShownSource(opts) {
  const source = getActiveSource();
  if (source === shownSource) return false;
  shownSource = source;
  if (!opts.refetch) return false;
  scheduleInventoryRefresh().catch((e) =>
    AppLog.warn('sources: refresh after moving to ' + source + ' failed: ' + e));
  return true;
}

/**
 * Write the tab set into preferences, and — when what this window shows has
 * changed — save it as the hub's header-less default too.
 *
 * Called from `publishTabs` rather than from `switchToTab` because a switch is
 * not the only thing that changes what is on screen: closing the front tab,
 * grouping, and a roster edit that deletes the source a tab was showing all do.
 * Doing it in one place is what stops the hub's saved default naming a server
 * this window can no longer reach.
 *
 * Its `changed` answer is about the SAVED DEFAULT, not about the grid — see
 * `syncShownSource` above, which is what decides a refetch. Conflating the two
 * is the bug that left a closed tab's rows on screen.
 * @returns {boolean} whether the saved default changed
 */
function persistTabs() {
  const { tabs, activeTabId } = activeSourceSignal.peek();
  const source = getActiveSource() || LOCAL_ID;
  const changed = preferences.active_source !== source;
  preferences.server_tabs = tabs.map((t) => ({
    id: t.id, sources: t.sources.slice(), name: t.name, view: t.view,
  }));
  preferences.active_tab = activeTabId;
  preferences.active_source = source;
  savePreferences();
  preferencesSignal.set(preferences);
  if (changed) {
    // Not awaited, and a failure is a warning rather than an error: this is the
    // default for clients that send NO header (tools/dubis-cli, curl, the next
    // launch). This window already names its own source on every request, so it
    // is unaffected either way. `PUT /v1/sources/active` publishes no SSE for
    // the same reason — announcing it would make every other window re-fetch
    // data that did not move.
    Promise.resolve(api('set_active_source', source)).then((r) => {
      if (r === undefined) {
        AppLog.warn('sources: could not save "' + source + '" as the default for new clients');
      }
    });
  }
  return changed;
}

/**
 * Re-point `server_url` at whatever single server the active tab shows.
 *
 * A group leaves it alone: that key answers "which ONE server", and there is no
 * honest answer for a merged view — blanking it would silently re-point
 * `tools/dubis-cli` and the next launch at local.
 */
function syncServerUrl() {
  const tab = getActiveTab();
  if (!tab || tab.sources.length !== 1) return;
  const id = tab.sources[0];
  if (id === LOCAL_ID) {
    preferences.server_url = '';
    return;
  }
  const target = getSources().find((s) => s.id === id) || getServers().find((s) => s.id === id);
  if (target) preferences.server_url = normalizeServerUrl(target.url);
}

/**
 * Bring the tab set in line with a roster that just changed, then republish.
 * @param {Array<import('./signals.js').SourceEntry>} sources
 * @param {{persist?: boolean}} [opts]
 */
function reconcileWithSources(sources, opts) {
  const state = activeSourceSignal.peek();
  const before = allSourceIds(state.sources);
  const after = allSourceIds(sources);
  let tabs = reconcileTabs(state.tabs, before, after);
  if (!tabs.length) tabs = seedTabs(after, newTabId);
  return publishTabs(tabs, state.activeTabId, sources, opts);
}

/**
 * Re-run the roster → tabs reconciliation after a Preferences roster edit.
 *
 * Kept separate from `hydrateSourcesFromPreferences` because this one PERSISTS:
 * an add/rename/remove is a change the user made, and the tab that lost its
 * server has to stay lost across a restart.
 * @returns {string} the resolved active tab id
 */

/**
 * Seed the signal from what is already on disk, with no network call.
 *
 * Called at startup before `loadSources()` answers so the strip renders its tabs
 * on the first frame instead of appearing a round trip later. Every dot reads
 * "unknown" here, which is the truth: the preferences roster is a list of URLs
 * somebody typed and nothing has contacted any of them.
 */
/**
 * The source id whose URL is `url`, or "" — the bridge from `server_url` (a URL)
 * to a tab (which names ids).
 * @param {Array<import('./signals.js').SourceEntry>} sources
 * @param {string} url
 * @returns {string}
 */
function sourceIdForUrl(sources, url) {
  const want = normalizeServerUrl(url);
  if (!want) return '';
  const match = (sources || []).find((s) => s.url === want);
  return match ? match.id : '';
}

export function syncTabsWithRoster() {
  return reconcileWithSources(rosterSyncedSources());
}

export function hydrateSourcesFromPreferences() {
  const sources = rosterSyncedSources();
  const ids = allSourceIds(sources);
  let tabs = normalizeTabs(preferences.server_tabs, ids, function (msg) {
    AppLog.warn('server_tabs: ' + msg);
  });
  // Nothing persisted — either a first run or an upgrade from the roster-derived
  // strip. seedTabs reproduces exactly what that strip showed, so nobody's tabs
  // change on upgrade.
  if (!tabs.length) tabs = seedTabs(ids, newTabId);
  // No `active_tab` means either a first run or an upgrade from the release
  // that had no tabs at all — and that release persisted `active_source`. Honour
  // it, or a user who was looking at `bench` would come back to Local and be
  // told nothing about why.
  const active = preferences.active_tab
    || tabForSourceValue(tabs, ids, preferences.active_source)
    // Last resort, and the case `active_source` cannot cover: a DUBIS_URL launch
    // or a hand-edited preferences.json, where nobody ever recorded a choice but
    // `server_url` demonstrably points somewhere.
    || tabForSource(tabs, sourceIdForUrl(sources, preferences.server_url))
    || '';
  // refresh: false — hydration is the startup seed, not a move. loadInventory()
  // is about to fetch with exactly this source; refetching here would cost every
  // launch a duplicate round trip.
  return publishTabs(tabs, active, sources, { persist: false, refresh: false });
}

/**
 * Fetch the hub's source roster (`GET /v1/sources`) and publish it.
 *
 * Degrades to the preferences roster rather than to an empty strip. The route is
 * newer than the roster it describes, so a hub that does not serve it yet (or a
 * browser client pointed at an older deployment) must still get a working
 * switcher — just one whose dots admit they know nothing.
 * @returns {Promise<string>} the resolved active tab id
 */
export async function loadSources() {
  const payload = await api('list_sources');
  if (payload === undefined) {
    AppLog.warn('sources: GET /v1/sources is unavailable — using the saved roster');
    return hydrateSourcesFromPreferences();
  }
  const { sources, defaultSource } = normalizeSources(payload, function (msg) {
    AppLog.warn('list_sources: ' + msg);
  });
  // persist: false — this is the hub telling us about reachability, which is not
  // a change the user made to their tabs.
  const active = reconcileWithSources(sources, { persist: false });
  // Only for a window that has never chosen: land it on the hub's persisted
  // default (a DUBIS_URL launch, say) instead of on whichever tab was seeded
  // first. A window that HAS chosen ignores the default forever after — it
  // names its own source on every request, which is what makes a second window
  // attaching to this hub independent of it.
  if (!preferences.active_tab && defaultSource) {
    const state = activeSourceSignal.peek();
    const wanted = tabForSourceValue(state.tabs, allSourceIds(state.sources), defaultSource);
    if (wanted && wanted !== active) return switchToTab(wanted).then(() => wanted);
  }
  return active;
}

/**
 * Bring a tab to the front.
 *
 * The switch is LOCAL, and that is the whole design. server/dispatch.py keeps no
 * mutable "current server": every `/v1` request names its source with
 * `X-Dubis-Source`, which js/api.js takes from `getActiveSource()` — so the
 * moment this function moves the signal, every subsequent request is already
 * being served from the new tab's sources. Nothing had to be negotiated with the
 * hub for the switch itself.
 *
 * `PUT /v1/sources/active` is therefore *not* the switch. It saves the
 * header-less default, for `tools/dubis-cli`, for curl, and for the next launch,
 * and it publishes no SSE event on purpose — announcing it would make every
 * OTHER window re-fetch data that did not move. It is fired without blocking,
 * and a failure to save it is a warning, not a failed switch: this window has
 * demonstrably switched either way, and refusing to believe that would leave the
 * strip pointing at a tab whose data is already on screen.
 *
 * The refresh is `scheduleInventoryRefresh()`, the same debounced path every
 * mutation and every `inventory.updated` push uses — and since the hub stays
 * silent about a default write, this direct call is the ONLY thing that
 * re-renders after a switch. That is correct: only the switching window's data
 * moved.
 *
 * This is the ONE entry point for changing what the grid shows. The tab strip
 * and the Preferences roster's `Use` button are two gestures for the same
 * action, and routing both through here is what stops them drifting into
 * meaning different things — which is exactly what happened while one of them
 * offered a restart.
 * @param {string} tabId
 * @returns {Promise<{ok: boolean, reason?: string, tabId?: string, source?: string}>}
 */
export async function switchToTab(tabId) {
  const state = activeSourceSignal.peek();
  const tab = state.tabs.find((t) => t.id === tabId);
  if (!tab) return { ok: false, reason: 'That tab is no longer open' };

  const source = activeSourceValue(tab, allSourceIds(state.sources));
  // Through publishTabs like every other gesture: it re-points server_url,
  // persists, and refetches only when the SOURCE moved. Two tabs on the same
  // server show the same rows through different filters, so a switch between
  // them is a re-render (driven by the view the caller applies), never a round
  // trip.
  publishTabs(state.tabs, tabId, state.sources);
  return { ok: true, tabId, source };
}

/**
 * Open a tab, next to the active one, and switch to it.
 * @param {string[]} [sources] defaults to the active tab's sources — `+` on a
 *   server you are looking at gives you a second view of that same server,
 *   which is the whole reason a tab id is not a source id.
 * @returns {Promise<{ok: boolean, reason?: string, tabId?: string}>}
 */
export async function openTab(sources) {
  const state = activeSourceSignal.peek();
  const known = new Set(allSourceIds(state.sources));
  const active = state.tabs.find((t) => t.id === state.activeTabId);
  const want = (sources && sources.length ? sources : (active ? active.sources : [LOCAL_ID]))
    .filter((id) => known.has(id));
  if (!want.length) return { ok: false, reason: 'That server is no longer in the list' };
  const tab = { id: newTabId(), sources: want, name: '', view: null };
  publishTabs(addTab(state.tabs, tab, state.activeTabId), state.activeTabId, state.sources);
  return switchToTab(tab.id);
}

/**
 * Close a tab. Switches away first when it is the one in front, so the hub is
 * never left serving a tab that no longer exists.
 * @param {string} tabId
 * @returns {Promise<{ok: boolean, reason?: string}>}
 */
export async function closeTabById(tabId) {
  const state = activeSourceSignal.peek();
  const result = closeTab(state.tabs, state.activeTabId, tabId);
  if (!result.ok) return { ok: false, reason: result.reason };
  const wasActive = state.activeTabId === tabId;
  publishTabs(result.tabs, result.activeTabId, state.sources);
  if (wasActive) return switchToTab(result.activeTabId).then(() => ({ ok: true }));
  return { ok: true };
}

/**
 * Reorder: move `tabId` so it sits before `beforeId` ("" = last).
 * @param {string} tabId
 * @param {string} beforeId
 */
export function moveTabBefore(tabId, beforeId) {
  const state = activeSourceSignal.peek();
  publishTabs(moveTab(state.tabs, tabId, beforeId), state.activeTabId, state.sources);
}

/**
 * Merge several tabs into one group tab, and switch to it.
 * @param {string[]} tabIds
 * @returns {Promise<{ok: boolean, reason?: string, tabId?: string}>}
 */
export async function groupTabsById(tabIds) {
  const state = activeSourceSignal.peek();
  const result = groupTabs(state.tabs, tabIds, newTabId());
  if (!result.ok) return { ok: false, reason: result.reason };
  publishTabs(result.tabs, result.activeTabId, state.sources);
  return switchToTab(result.activeTabId);
}

/**
 * Record a tab's inventory view snapshot (search, chips, sort, grouping).
 *
 * Kept off the signal's change path on purpose: capturing the outgoing tab's
 * view happens on every switch, and re-notifying every effect for it would
 * re-render the strip in the middle of a switch for a change nothing renders.
 * @param {string} tabId
 * @param {object|null} view a `captureView()` snapshot
 */
export function setTabView(tabId, view) {
  const state = activeSourceSignal.peek();
  const tab = state.tabs.find((t) => t.id === tabId);
  if (!tab) return;
  tab.view = view;
  persistTabs();
}

/**
 * Switch to whichever tab shows this server, opening one if none does.
 *
 * The Preferences roster picks a *server*, not a tab, so it comes through here.
 * It activates an existing plain tab rather than re-pointing the tab in front:
 * re-pointing would silently change what a tab means, and that tab's saved view
 * belongs to the server it was opened for.
 * @param {string} sourceId `"local"` or a roster entry id
 * @returns {Promise<{ok: boolean, reason?: string, tabId?: string}>}
 */
export async function switchActiveSource(sourceId) {
  const state = activeSourceSignal.peek();
  if (!allSourceIds(state.sources).includes(sourceId)) {
    return { ok: false, reason: 'That server is no longer in the list' };
  }
  const existing = tabForSource(state.tabs, sourceId);
  if (existing) return switchToTab(existing);
  return openTab([sourceId]);
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
