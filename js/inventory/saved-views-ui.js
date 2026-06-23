// @ts-check
/**
 * js/inventory/saved-views-ui.js — "Views ▾" toolbar dropdown for the inventory panel.
 *
 * Renders a compact dropdown button that lists saved views; clicking a view
 * name applies it immediately. A "Save current view…" item opens a form-modal
 * name prompt (dogfoods Phase 2b defineFormModal).
 *
 * Exports:
 *   initSavedViewsUI(state, renderFn, updateDistFilterUI)
 *     → call once after inventory panel init; injects the button into the toolbar.
 */

import { escapeHtml } from '../dom/html.js';
import { on } from '../dom/delegate.js';
import { AppLog } from '../api.js';
import { defineFormModal } from '../components/form-modal.js';
import { captureView, applyView, saveView, listViews, deleteView } from './saved-views.js';

/** @type {string|null} Active (most-recently applied) view id, for UI indicator. */
let _activeViewId = null;

/** @type {HTMLElement|null} Currently open menu element. */
let _openMenu = null;

/** @type {Array<() => void>} Cleanup functions for the current menu. */
let _menuRemovers = [];

// ── Name-prompt form-modal (created lazily once) ──────────────────────────────

let _nameFm = null;

/**
 * Return (creating if needed) the "Save view" name form-modal.
 * @param {() => void} onSaved  Called after a view is successfully saved.
 */
function getNameModal(onSaved) {
  if (_nameFm) return _nameFm;
  _nameFm = defineFormModal('sv-name-modal', {
    title: 'Save current view',
    fields: [
      { key: 'name', label: 'View name', type: 'text', placeholder: 'e.g. LCSC capacitors' },
    ],
    onPopulate: () => ({ name: '' }),
    validate: (values) => {
      if (!values.name || !values.name.trim()) return { name: 'Enter a name' };
      return null;
    },
    onConfirm: async (values, ctx) => {
      await saveView(values.name.trim(), ctx.snapshot);
      onSaved();
      return true;
    },
    successToast: (values) => 'View "' + values.name.trim() + '" saved',
    confirmLabel: 'Save',
  });
  return _nameFm;
}

// ── Menu close ────────────────────────────────────────────────────────────────

function closeMenu() {
  if (_openMenu) {
    _openMenu.remove();
    _openMenu = null;
  }
  for (const r of _menuRemovers) r();
  _menuRemovers = [];
}

// ── Menu open ─────────────────────────────────────────────────────────────────

/**
 * Open the views dropdown menu below the given anchor element.
 * @param {HTMLElement} anchor
 * @param {object} state  Inventory panel state (passed through to apply/capture).
 * @param {() => void} renderFn
 * @param {() => void} updateDistFilterUI
 * @param {HTMLElement} btn  The "Views ▾" button (to update its label after apply/save).
 */
function openMenu(anchor, state, renderFn, updateDistFilterUI, btn) {
  if (_openMenu) { closeMenu(); return; } // toggle off

  const views = listViews();

  const menu = document.createElement('div');
  menu.className = 'saved-views-menu';
  menu.setAttribute('role', 'menu');

  let html = '';

  if (views.length > 0) {
    html += '<div class="sv-menu-section-header">Saved views</div>';
    for (const v of views) {
      const isActive = v.id === _activeViewId;
      html += `<div class="sv-menu-item${isActive ? ' sv-active' : ''}" role="menuitem" tabindex="-1" data-view-id="${escapeHtml(v.id)}">` +
        `<span class="sv-view-name">${escapeHtml(v.name)}</span>` +
        `<button class="sv-delete-btn" data-delete-id="${escapeHtml(v.id)}" title="Delete view" tabindex="-1">×</button>` +
        `</div>`;
    }
    html += '<div class="sv-menu-divider"></div>';
  } else {
    html += '<div class="sv-empty-msg">No saved views yet</div>';
    html += '<div class="sv-menu-divider"></div>';
  }

  html += '<div class="sv-save-item" role="menuitem" tabindex="-1" data-action="save-view">+ Save current view…</div>';

  menu.innerHTML = html;

  // Position below the anchor
  anchor.appendChild(menu);
  _openMenu = menu;

  // ── Delegated click: apply a view ────────────────────────────────────────────
  const removeApply = on(menu, 'click', '.sv-menu-item', (e, el) => {
    // If the click was on the delete button, don't apply
    if (/** @type {HTMLElement} */ (e.target).closest('.sv-delete-btn')) return;
    const id = /** @type {HTMLElement} */ (el).dataset.viewId;
    if (!id) return;
    const view = listViews().find(v => v.id === id);
    if (!view) return;
    _activeViewId = id;
    applyView(view, state);
    updateDistFilterUI();
    renderFn();
    updateBtn(btn);
    closeMenu();
  });
  _menuRemovers.push(removeApply);

  // ── Delegated click: delete a view ───────────────────────────────────────────
  const removeDelete = on(menu, 'click', '.sv-delete-btn', async (e, el) => {
    e.stopPropagation();
    const id = /** @type {HTMLElement} */ (el).dataset.deleteId;
    if (!id) return;
    if (_activeViewId === id) _activeViewId = null;
    await deleteView(id).catch(err => AppLog.warn('saved-views: delete failed: ' + err));
    closeMenu();
    // Re-open to reflect the updated list
    openMenu(anchor, state, renderFn, updateDistFilterUI, btn);
    updateBtn(btn);
  });
  _menuRemovers.push(removeDelete);

  // ── Delegated click: save current view ──────────────────────────────────────
  const removeSave = on(menu, 'click', '[data-action="save-view"]', () => {
    closeMenu();
    const snapshot = captureView(state);
    const fm = getNameModal(() => {
      updateBtn(btn);
    });
    fm.open({ snapshot });
  });
  _menuRemovers.push(removeSave);

  // ── Outside-click closes ─────────────────────────────────────────────────────
  // Use setTimeout to skip the current event that opened the menu
  setTimeout(() => {
    const onDocClick = (/** @type {MouseEvent} */ e) => {
      if (_openMenu && !_openMenu.contains(/** @type {Node} */ (e.target)) &&
          !btn.contains(/** @type {Node} */ (e.target))) {
        closeMenu();
      }
    };
    document.addEventListener('click', onDocClick);
    _menuRemovers.push(() => document.removeEventListener('click', onDocClick));
  }, 0);

  // ── Esc closes ───────────────────────────────────────────────────────────────
  const onKeydown = (/** @type {KeyboardEvent} */ e) => {
    if (e.key === 'Escape') { closeMenu(); btn.focus(); }
  };
  document.addEventListener('keydown', onKeydown);
  _menuRemovers.push(() => document.removeEventListener('keydown', onKeydown));
}

// ── Button label updater ──────────────────────────────────────────────────────

/**
 * Update the "Views ▾" button to reflect active view name (if any).
 * @param {HTMLElement} btn
 */
function updateBtn(btn) {
  const views = listViews();
  const activeView = _activeViewId ? views.find(v => v.id === _activeViewId) : null;

  let label = '<span class="sv-btn-label">Views</span>';
  if (activeView) {
    label = '<span class="sv-btn-label">Views</span>' +
      '<span class="sv-active-name" title="' + escapeHtml(activeView.name) + '">' +
      escapeHtml(activeView.name) + '</span>';
  }
  label += '<span class="sv-caret">▾</span>';

  btn.innerHTML = label;
  btn.classList.toggle('has-active', !!activeView);
}

// ── Public init ───────────────────────────────────────────────────────────────

/**
 * Inject the "Views ▾" button into the inventory panel header and wire it up.
 *
 * @param {object} state  Inventory panel state.
 * @param {() => void} renderFn  Trigger a full inventory re-render.
 * @param {() => void} updateDistFilterUI  Sync distributor pill active states.
 */
export function initSavedViewsUI(state, renderFn, updateDistFilterUI) {
  // Insert button into panel header, before the search group
  const header = document.querySelector('.panel-inventory .panel-header');
  if (!header) {
    AppLog.warn('saved-views-ui: panel-inventory .panel-header not found');
    return;
  }

  // Create anchor wrapper (provides positioning context for the dropdown)
  const anchor = document.createElement('div');
  anchor.className = 'saved-views-anchor';

  const btn = document.createElement('button');
  btn.className = 'saved-views-btn';
  btn.setAttribute('type', 'button');
  btn.setAttribute('title', 'Saved views');
  btn.setAttribute('aria-haspopup', 'true');
  btn.setAttribute('aria-expanded', 'false');
  btn.setAttribute('id', 'saved-views-btn');
  updateBtn(btn);

  anchor.appendChild(btn);

  // Insert before the search group div
  const searchGroup = header.querySelector('.inv-search-group');
  if (searchGroup) {
    header.insertBefore(anchor, searchGroup);
  } else {
    header.appendChild(anchor);
  }

  // Wire button click
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const isOpen = !!_openMenu;
    btn.setAttribute('aria-expanded', String(!isOpen));
    openMenu(anchor, state, renderFn, updateDistFilterUI, btn);
  });

  // Expose clear-active-view helper for external use (e.g. BOM_CLEARED)
  /** @type {any} */ (btn)._clearActiveView = () => {
    _activeViewId = null;
    updateBtn(btn);
  };
}
