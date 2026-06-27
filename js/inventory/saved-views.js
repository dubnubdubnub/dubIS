// @ts-check
/**
 * js/inventory/saved-views.js — Named saved-view management for the inventory panel.
 *
 * A "view" is a named snapshot of the current filter/sort/group state.
 * Views are persisted in `store.preferences.saved_views` via the existing
 * `savePreferences()` mechanism (→ preferences.json).
 *
 * Exports:
 *   captureView(state)           → snapshot object from live inventory state
 *   applyView(view, state)       → mutate state to match the view; caller calls render()
 *   saveView(name, snapshot)     → append to saved_views + persist
 *   listViews()                  → filtered list of valid saved views
 *   deleteView(id)               → remove by id + persist
 *   renameView(id, name)         → update name + persist
 */

import { store, savePreferences } from '../store.js';
import { AppLog } from '../api.js';

// ── ID generation ──────────────────────────────────────────────────────────

/**
 * Generate a simple unique string id.
 * @returns {string}
 */
function genId() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
}

// ── captureView ────────────────────────────────────────────────────────────

/**
 * Snapshot the current inventory panel state into a plain serialisable object.
 *
 * Pure-ish: state is passed in; no globals touched.
 *
 * @param {{
 *   searchInput: { value: string },
 *   activeDistributors: Set<string>,
 *   groupLevel: number,
 *   sortColumn: string|null,
 *   sortScope: string|null,
 *   vendorGroupScope: string|null,
 *   activePredicate?: any,
 * }} state
 * @returns {{
 *   searchTerm: string,
 *   distributors: string[],
 *   groupLevel: number,
 *   sortColumn: string|null,
 *   sortScope: string|null,
 *   vendorGroupScope: string|null,
 *   predicate: any,
 * }}
 */
export function captureView(state) {
  return {
    searchTerm: state.searchInput.value,
    distributors: [...state.activeDistributors],
    groupLevel: state.groupLevel,
    sortColumn: state.sortColumn,
    sortScope: state.sortScope,
    vendorGroupScope: state.vendorGroupScope,
    predicate: state.activePredicate ? JSON.parse(JSON.stringify(state.activePredicate)) : null,
  };
}

// ── applyView ──────────────────────────────────────────────────────────────

/**
 * Mutate `state` to match the given view snapshot.
 * Caller must call render() (and updateDistFilterUI() if needed) afterwards.
 *
 * Pure-ish: only touches the passed-in state object.
 *
 * @param {{
 *   searchTerm?: string,
 *   distributors?: string[],
 *   groupLevel?: number,
 *   sortColumn?: string|null,
 *   sortScope?: string|null,
 *   vendorGroupScope?: string|null,
 *   predicate?: any,
 * }} view
 * @param {{
 *   searchInput: { value: string },
 *   activeDistributors: Set<string>,
 *   groupLevel: number,
 *   sortColumn: string|null,
 *   sortScope: string|null,
 *   vendorGroupScope: string|null,
 *   activePredicate?: any,
 * }} state
 */
export function applyView(view, state) {
  state.searchInput.value = view.searchTerm || '';
  state.activeDistributors.clear();
  for (const d of (view.distributors || [])) {
    state.activeDistributors.add(d);
  }
  state.groupLevel = typeof view.groupLevel === 'number' ? view.groupLevel : 0;
  state.sortColumn = view.sortColumn !== undefined ? view.sortColumn : null;
  state.sortScope = view.sortScope !== undefined ? view.sortScope : null;
  state.vendorGroupScope = view.vendorGroupScope !== undefined ? view.vendorGroupScope : null;
  // Restore predicate filter chips
  state.activePredicate = view.predicate ? JSON.parse(JSON.stringify(view.predicate)) : null;
  // Sync filter chips bar UI to restored predicate
  const _stateRef = /** @type {any} */ (state);
  import('./filter-chips-bar.js').then(function (m) {
    if (typeof m.syncFilterChipsBar === 'function') m.syncFilterChipsBar(_stateRef);
  }).catch(function () { /* filter chips bar not loaded yet */ });
}

// ── Ensure saved_views array ───────────────────────────────────────────────

/**
 * Ensure store.preferences.saved_views is a valid array, resetting it if not.
 * @returns {Array<object>}
 */
function ensureArray() {
  if (!Array.isArray(store.preferences.saved_views)) {
    store.preferences.saved_views = [];
  }
  return store.preferences.saved_views;
}

// ── listViews ──────────────────────────────────────────────────────────────

/**
 * Return the list of valid saved views, filtering out malformed entries.
 * Malformed entries (missing id or name) are logged and skipped, not crashed on.
 *
 * @returns {Array<{id:string, name:string, searchTerm:string, distributors:string[], groupLevel:number, sortColumn:string|null, sortScope:string|null, vendorGroupScope:string|null, predicate:null}>}
 */
export function listViews() {
  if (!Array.isArray(store.preferences.saved_views)) {
    return [];
  }

  /** @type {Array<object>} */
  const valid = [];
  for (const entry of store.preferences.saved_views) {
    if (!entry || typeof entry !== 'object') {
      AppLog.warn('saved-views: skipping non-object entry');
      continue;
    }
    if (!entry.id || typeof entry.id !== 'string') {
      AppLog.warn('saved-views: skipping entry with missing or invalid id');
      continue;
    }
    if (!entry.name || typeof entry.name !== 'string') {
      AppLog.warn('saved-views: skipping entry "' + entry.id + '" with missing or invalid name');
      continue;
    }
    valid.push(entry);
  }
  return valid;
}

// ── saveView ───────────────────────────────────────────────────────────────

/**
 * Append a new named view to saved_views and persist preferences.
 *
 * @param {string} name  Display name for the view.
 * @param {{
 *   searchTerm: string,
 *   distributors: string[],
 *   groupLevel: number,
 *   sortColumn: string|null,
 *   sortScope: string|null,
 *   vendorGroupScope: string|null,
 *   predicate: null,
 * }} snapshot  From captureView().
 * @returns {Promise<void>}
 */
export async function saveView(name, snapshot) {
  const views = ensureArray();
  views.push({
    id: genId(),
    name,
    ...snapshot,
  });
  await savePreferences();
}

// ── deleteView ─────────────────────────────────────────────────────────────

/**
 * Remove the saved view with the given id and persist preferences.
 * No-op if id is not found.
 *
 * @param {string} id
 * @returns {Promise<void>}
 */
export async function deleteView(id) {
  const views = ensureArray();
  const idx = views.findIndex(v => v.id === id);
  if (idx === -1) return; // no-op
  views.splice(idx, 1);
  await savePreferences();
}

// ── renameView ─────────────────────────────────────────────────────────────

/**
 * Rename an existing saved view and persist preferences.
 * No-op if id is not found.
 *
 * @param {string} id
 * @param {string} name
 * @returns {Promise<void>}
 */
export async function renameView(id, name) {
  const views = ensureArray();
  const entry = views.find(v => v.id === id);
  if (!entry) return; // no-op
  entry.name = name;
  await savePreferences();
}
