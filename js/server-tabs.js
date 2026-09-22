// @ts-check
/* server-tabs.js — the server quick-switcher: a strip of browser-style tabs
   under the header. Each tab is a view instance over one or more dubIS servers,
   switched live — no restart, no page reload.

     [● Local ×] [● bench ×] [◐ bench + shop ×]  [+]

   DOM wiring only. Every decision — tab order, labels, what a dot may claim,
   what a click/drag/group means — lives in js/server-tabs-logic.js, the same
   split js/server-list.js has with js/servers-logic.js.

   Four things about this file are load-bearing:

   1. It renders into `#server-tabs`, which sits in its OWN row BELOW `.header`,
      not inside it. `.header` is `flex-wrap: wrap` (css/layout.css), so a
      control added inside it costs a whole extra header row at narrow widths
      and shifts every panel down far enough to push the Import button off an
      800x600 viewport — tests/js/e2e/resize-visibility.spec.mjs catches exactly
      that. A sibling row is a row either way, so it is also hidden below the
      app's documented 1200x700 minimum, following the `ui-zoom` precedent
      (css/components/ui-zoom.css); the Preferences server picker stays
      reachable there and does the same thing. However many tabs are open, they
      stay on ONE line and scroll sideways.

   2. It never re-renders the inventory itself. A switch calls `switchToTab()`
      (js/store.js), which goes through `scheduleInventoryRefresh()` — the single
      debounced inventory re-render path this app has. A second one here would
      double-fetch on every switch. Note that the switch is LOCAL: the hub keeps
      no current-server state, every request names its own source with
      `X-Dubis-Source`, and `PUT /v1/sources/active` only saves the header-less
      default and publishes no SSE. So that direct call is the ONLY thing that
      refetches after a switch — correct, because only this window moved.

   3. It reads `activeSourceSignal` (js/signals.js) rather than listening for an
      event. Which tab is active is cross-panel *state*: the Preferences roster
      shows it too, and either can change it. An effect re-renders this strip
      whenever the other one moves, and — unlike an EventBus listener — is
      correct for a switch that happened before this module mounted.

   4. **The drag does no px arithmetic that could cross the zoom seam.** Under
      `html { zoom: z }` (js/ui-zoom.js) a `clientX` and a
      `getBoundingClientRect()` are *post-zoom*, while `offsetWidth` and any px
      written to a style are *authored* — mixing them displaces things by
      exactly the zoom factor, and looks flawless at 100% where the two spaces
      coincide. Two defences here: the drop position is decided by
      `dropTarget()`, which is handed BOTH numbers already converted to authored
      px (`toInnerPx(e.clientX)` and `innerRect(el)`), and the drag itself writes
      no px at all — the browser draws the drag image, and the drop indicator is
      a CSS class. The ONE place this file writes px is `positionMenu()`, and it
      is in authored space end to end (`innerRect` for the anchor,
      `zoomedViewport` for the clamp), exactly as js/text-popover.js does it — a
      raw `getBoundingClientRect()` or `window.innerWidth` there would put the
      menu a zoom factor away from the `+` it belongs to.
      `tests/js/zoom-geometry-guard.test.js` enforces both halves across js/.

   5. **The `+` menu lives on `document.body`, not in the strip.** The strip is
      `overflow-x: auto; overflow-y: hidden`, so a dropdown parented inside it
      would be clipped to the strip's own height — a menu you could see the top
      1px of. That is also why it cannot borrow the CSS-only anchoring the saved
      views menu uses, and why it is positioned instead.
*/

import { apiSupports, AppLog } from './api.js';
import { escHtml, showToast } from './ui-helpers.js';
import { activeSourceSignal, effect } from './signals.js';
import { innerRect, toInnerPx, zoomedViewport } from './ui-zoom.js';
import {
  switchToTab,
  openTab,
  getActiveTabId,
  closeTabById,
  moveTabBefore,
  groupTabsById,
  setTabView,
  loadSources,
  hydrateSourcesFromPreferences,
  getActiveTab,
} from './store.js';
import {
  renderTabs,
  shouldShowTabs,
  switchIntent,
  switchedMessage,
  selectionAfterClick,
  allSourceIds,
  dropTarget,
  newTabChoices,
} from './server-tabs-logic.js';
import invState from './inventory/inv-state.js';
import { captureView, applyView } from './inventory/saved-views.js';

/**
 * The view a brand-new tab starts from: everything cleared.
 *
 * A second tab on the server you are already looking at must NOT inherit its
 * filters — inheriting them would make `+` a duplicate button, when the reason
 * to open the same server twice is to look at it two different ways.
 */
const EMPTY_VIEW = Object.freeze({
  searchTerm: '', distributors: [], groupLevel: 0,
  sortColumn: null, sortScope: null, vendorGroupScope: null, predicate: null,
});

/**
 * Save the view of whatever tab is in front, then hand back a function that puts
 * the NEW front tab's view on screen.
 *
 * Every gesture that changes the front tab goes through this pair — switch, `+`,
 * group, close — because each of them leaves a different tab in front, and a
 * tab that inherits the previous one's filters is indistinguishable from a bug.
 * Capturing happens BEFORE the await: the filters belong to the tab being left,
 * and an await is a window in which the user could keep typing into them.
 * @returns {() => void}
 */
function carryView() {
  setTabView(getActiveTabId(), snapshotView());
  return () => {
    const tab = getActiveTab();
    restoreView(tab ? tab.view : null);
  };
}

/** Guard against a second click landing while a switch is in flight. */
let switching = false;
/** Tab ids in the multi-selection (ctrl/shift-click). Transient — never persisted. */
let selected = [];
/** The id a shift-click extends a range from. */
let anchor = '';
/** During a drag: the id the dragged tab would land before, "" for last. */
let dropBefore = '';

/** Gap between the `+` and its menu, and the window margin the menu keeps. */
const MENU_GAP_PX = 4;
const MENU_EDGE_PX = 8;

function stripEl() {
  return document.getElementById('server-tabs');
}

// ── Per-tab view state ────────────────────────────────────
// captureView/applyView (js/inventory/saved-views.js) already snapshot exactly
// the shape a tab needs — search, distributor pills, group level, sort, vendor
// grouping and the filter-chip predicate. Reusing them rather than inventing a
// parallel snapshot is what keeps a tab's view and a *saved* view from drifting
// into two different ideas of "the current view".

/** @returns {object|null} the live view, or null before the inventory panel mounts. */
function snapshotView() {
  if (!invState || !invState.searchInput) return null;
  try {
    return captureView(/** @type {any} */ (invState));
  } catch (e) {
    AppLog.warn('server-tabs: could not capture the current view: ' + e.message);
    return null;
  }
}

/**
 * Put a tab's view on screen.
 * @param {object|null} view
 * @param {{render?: boolean}} [opts] `render: false` at startup, where the
 *   inventory's own first load is about to render anyway.
 */
function restoreView(view, opts) {
  if (!invState || !invState.searchInput) return;
  try {
    applyView(/** @type {any} */ (view || EMPTY_VIEW), /** @type {any} */ (invState));
  } catch (e) {
    AppLog.warn('server-tabs: could not apply the tab view: ' + e.message);
    return;
  }
  if (opts && opts.render === false) return;
  // The same signal js/app-init.js's command palette uses to apply a saved
  // view: inv-events.js answers it with one render(). Calling invState._render()
  // as well would render twice for one switch.
  window.dispatchEvent(new CustomEvent('inv-filter-changed'));
}

// ── Render ────────────────────────────────────────────────

/** @param {import('./server-tabs-logic.js').TabView} tab */
function tabHtml(tab) {
  return `
    <div class="server-tab${tab.active ? ' selected' : ''}${tab.selected ? ' multi' : ''}"
         draggable="true" data-tab-id="${escHtml(tab.id)}"
         role="button" tabindex="0" aria-pressed="${tab.active ? 'true' : 'false'}"
         title="${escHtml(tab.title + ' — ' + tab.dot.detail)}">
      <span class="server-tab-dot ${escHtml(tab.dot.state)}"></span>
      <span class="server-tab-name">${escHtml(tab.label)}</span>
      ${tab.closable
    ? `<button type="button" class="server-tab-close" data-act="close"
                 aria-label="Close ${escHtml(tab.label)}" title="Close this tab">&times;</button>`
    : ''}
    </div>`;
}

/** Rebuild the strip from the signal. Cheap — a handful of elements. */
function render() {
  const host = stripEl();
  if (!host) return;
  const { tabs, activeTabId, sources } = activeSourceSignal.get();
  // A selection can outlive the tabs it named (a close, or a roster edit that
  // emptied a tab), and a stale id would keep the Group button armed over
  // nothing.
  selected = selected.filter((id) => tabs.some((t) => t.id === id));
  // A one-tab selection is just "the tab you clicked" — drawing it as a
  // multi-selection would put a dashed border on the strip after every ordinary
  // click, which says nothing.
  const models = renderTabs(tabs, sources, activeTabId, selected.length > 1 ? selected : []);

  // `hidden` rather than emptying the element: an empty flex row still has
  // padding and a border, so it would keep costing the panels its height while
  // showing nothing. The CSS re-asserts it, because `display: flex` on the strip
  // would otherwise beat the `[hidden]` user-agent rule.
  const show = shouldShowTabs(tabs, allSourceIds(sources));
  host.hidden = !show;
  if (!show) {
    host.innerHTML = '';
    return;
  }
  host.innerHTML = models.map(tabHtml).join('')
    + '<button type="button" class="server-tab-add" data-act="add" aria-haspopup="menu"'
    + ' aria-expanded="' + (menuOpen() ? 'true' : 'false') + '"'
    + ' aria-label="Open a new tab" title="Open a new tab on a server">+</button>'
    + (selected.length > 1
      ? '<button type="button" class="server-tab-group" data-act="group">Group '
        + selected.length + '</button>'
      : '');
}

// ── The + menu ────────────────────────────────────────────
// `+` asks WHICH server before opening a tab. It used to open a second view of
// whatever was in front and nothing else, which meant the strip could not reach
// a server that had no tab yet — the roster lived in Preferences, three clicks
// away, for the one thing a tab strip exists to do.
//
// On document.body rather than inside the strip: the strip scrolls sideways
// (`overflow-x: auto; overflow-y: hidden`), so a child dropdown would be clipped
// to the strip's own height. That rules out the CSS-only anchoring
// js/inventory/saved-views-ui.js gets to use, so this one is positioned — in
// authored px, the js/text-popover.js way (see the header comment).

/** @type {HTMLElement|null} */
let menuEl = null;

function menuOpen() {
  return !!menuEl;
}

/** @param {import('./server-tabs-logic.js').TabChoice} choice */
function choiceHtml(choice) {
  return `
    <button type="button" class="server-tab-menu-item${choice.current ? ' current' : ''}"
            role="menuitem" data-choice="${escHtml(choice.key)}"
            title="${escHtml(choice.title)}">
      <span class="server-tab-dot ${escHtml(choice.dot.state)}"></span>
      <span class="server-tab-menu-name">${escHtml(choice.label)}</span>
      ${choice.current ? '<span class="server-tab-menu-hint">in front</span>' : ''}
    </button>`;
}

/**
 * Put the menu under the `+`, in authored px throughout.
 *
 * `innerRect` and `zoomedViewport` (js/ui-zoom.js), never a raw rect or
 * `window.innerWidth`: under `html { zoom: z }` those report post-zoom px while
 * the `style.left` written here is authored, and mixing the two puts the menu a
 * zoom factor away from the button it is supposed to hang off — invisibly
 * correct at 100%, wrong everywhere else.
 * @param {HTMLElement} anchorEl
 */
function positionMenu(anchorEl) {
  if (!menuEl) return;
  const rect = innerRect(anchorEl);
  const vp = zoomedViewport();
  const w = menuEl.offsetWidth || 180;
  const h = menuEl.offsetHeight || 120;
  let left = rect.left;
  let top = rect.bottom + MENU_GAP_PX;
  if (left + w > vp.w - MENU_EDGE_PX) left = vp.w - w - MENU_EDGE_PX;
  if (left < MENU_EDGE_PX) left = MENU_EDGE_PX;
  // Above the button when there is no room below — the strip sits at the top of
  // the window, so this is the rare case, but a menu hanging off the bottom of a
  // short window is unusable rather than merely ugly.
  if (top + h > vp.h - MENU_EDGE_PX) top = Math.max(MENU_EDGE_PX, rect.top - h - MENU_GAP_PX);
  menuEl.style.left = left + 'px';
  menuEl.style.top = top + 'px';
}

/** @param {HTMLElement} anchorEl the `+` button */
function openMenu(anchorEl) {
  closeMenu();
  const { sources } = activeSourceSignal.peek();
  const choices = newTabChoices(sources, getActiveTab());
  // One choice is not a choice: opening the menu would ask a question with a
  // single answer. Open the tab and be done.
  if (choices.length <= 1) {
    openNewTab(choices.length ? choices[0].sources : undefined);
    return;
  }
  menuEl = document.createElement('div');
  menuEl.className = 'server-tab-menu';
  menuEl.setAttribute('role', 'menu');
  menuEl.setAttribute('aria-label', 'Open a new tab on');
  menuEl.innerHTML = '<div class="server-tab-menu-head">New tab on</div>'
    + choices.map(choiceHtml).join('');
  menuEl.addEventListener('click', onMenuClick);
  document.body.appendChild(menuEl);
  positionMenu(anchorEl);
  anchorEl.setAttribute('aria-expanded', 'true');
  const first = /** @type {HTMLElement|null} */ (menuEl.querySelector('.server-tab-menu-item'));
  if (first) first.focus();
}

function closeMenu() {
  if (menuEl) menuEl.remove();
  menuEl = null;
  const host = stripEl();
  const add = host && host.querySelector('.server-tab-add');
  if (add) add.setAttribute('aria-expanded', 'false');
}

/**
 * Open a tab and put the tab's own (empty) view on screen.
 * @param {string[]} [sources] omitted = another view of the tab in front
 */
function openNewTab(sources) {
  const restore = carryView();
  return openTab(sources).then((r) => {
    if (!r.ok) { showToast(r.reason); return; }
    restore();
  });
}

/** @param {MouseEvent} e */
function onMenuClick(e) {
  const item = /** @type {HTMLElement|null} */ (
    /** @type {HTMLElement} */ (e.target).closest('[data-choice]')
  );
  if (!item || !menuEl) return;
  const key = item.dataset.choice || '';
  const choice = newTabChoices(activeSourceSignal.peek().sources, getActiveTab())
    .find((c) => c.key === key);
  closeMenu();
  if (!choice) { showToast('That server is no longer in the list'); return; }
  openNewTab(choice.sources);
}

/** Arrow-key roving plus the two ways out. @param {KeyboardEvent} e */
function onMenuKeydown(e) {
  if (!menuEl) return;
  if (e.key === 'Escape') {
    e.preventDefault();
    const host = stripEl();
    const add = /** @type {HTMLElement|null} */ (host && host.querySelector('.server-tab-add'));
    closeMenu();
    if (add) add.focus();
    return;
  }
  const items = [...menuEl.querySelectorAll('.server-tab-menu-item')];
  if (!items.length) return;
  const at = items.indexOf(/** @type {Element} */ (document.activeElement));
  let next = -1;
  if (e.key === 'ArrowDown') next = (at + 1) % items.length;
  else if (e.key === 'ArrowUp') next = (at - 1 + items.length) % items.length;
  else if (e.key === 'Home') next = 0;
  else if (e.key === 'End') next = items.length - 1;
  if (next === -1) return;
  e.preventDefault();
  /** @type {HTMLElement} */ (items[next]).focus();
}

// ── Clicking ──────────────────────────────────────────────

/** @param {MouseEvent} e */
function onClick(e) {
  const target = /** @type {HTMLElement} */ (e.target);
  const action = /** @type {HTMLElement | null} */ (target.closest('[data-act]'));
  const tabEl = /** @type {HTMLElement | null} */ (target.closest('[data-tab-id]'));
  // Any gesture other than the `+` itself dismisses its menu, including a
  // switch — a menu left hanging over a strip that has moved underneath it is
  // offering choices about a state that is gone.
  if (!action || action.dataset.act !== 'add') closeMenu();

  if (action && action.dataset.act === 'add') {
    // A menu, not an immediate open: `+` asks which server first. Picking the
    // one already in front is still "another view of this" — the gesture the
    // button used to be, now one of the answers rather than the only one. Either
    // way the new tab's VIEW starts clean; inheriting the filters would make
    // `+` a duplicate button, when the reason to open one server twice is to see
    // it two ways.
    if (menuOpen()) closeMenu();
    else openMenu(/** @type {HTMLElement} */ (action));
    return;
  }
  if (action && action.dataset.act === 'group') {
    const ids = selected.slice();
    const restore = carryView();
    groupTabsById(ids).then((r) => {
      if (!r.ok) { showToast(r.reason); return; }
      selected = [];
      anchor = '';
      restore();
      render();
    });
    return;
  }
  if (action && action.dataset.act === 'close' && tabEl) {
    const closingFront = (tabEl.dataset.tabId || '') === getActiveTabId();
    const restore = closingFront ? carryView() : null;
    closeTabById(tabEl.dataset.tabId || '').then((r) => {
      if (!r.ok) { showToast(r.reason); return; }
      // Only when the front tab went: closing a background one leaves the view
      // on screen exactly as it was, which is what the user expects.
      if (restore) restore();
    });
    return;
  }
  if (!tabEl) return;

  const id = tabEl.dataset.tabId || '';
  const { tabs, activeTabId } = activeSourceSignal.peek();

  // Ctrl/cmd or shift is a SELECTION gesture, not a navigation one: the user is
  // picking tabs to group, and switching the grid underneath them mid-selection
  // would refetch the inventory once per click.
  if (e.ctrlKey || e.metaKey || e.shiftKey) {
    const next = selectionAfterClick(
      tabs.map((t) => t.id), selected, anchor, id,
      { ctrl: e.ctrlKey || e.metaKey, shift: e.shiftKey },
    );
    selected = next.selected;
    anchor = next.anchor;
    render();
    return;
  }

  // A plain click selects exactly this tab, so a following ctrl-click extends
  // from it rather than starting over. The highlight stays quiet because render
  // only draws `multi` once there are two — a selection of one is just "the tab
  // you clicked".
  selected = [id];
  anchor = id;
  const intent = switchIntent(tabs, activeTabId, id);
  if (!intent.ok) {
    showToast(intent.reason);
    render();
    return;
  }
  // Clicking the tab you are on is a no-op, on purpose: firing the switch anyway
  // would make the active tab a refresh button nobody asked for, and each press
  // would cost an inventory refetch.
  if (!intent.changed) { render(); return; }
  if (switching) return;

  switching = true;
  const restore = carryView();
  const outgoingTabs = tabs;
  const { sources } = activeSourceSignal.peek();

  switchToTab(id)
    .then((result) => {
      if (!result.ok) {
        // The signal never moved, so re-rendering puts the highlight back on the
        // tab we are actually reading from — and the outgoing view stays on
        // screen, which is correct: we never left it.
        showToast(result.reason);
        render();
        return;
      }
      restore();
      showToast(switchedMessage(outgoingTabs, sources, id));
    })
    .catch((err) => {
      AppLog.error('server-tabs: switching to ' + id + ' failed: ' + err.message);
      render();
    })
    .finally(() => { switching = false; });
}

/** Space/Enter on a focused tab behaves like a click — the strip is keyboard-reachable. */
function onKeydown(e) {
  const key = /** @type {KeyboardEvent} */ (e).key;
  if (key !== 'Enter' && key !== ' ') return;
  const tabEl = /** @type {HTMLElement | null} */ (
    /** @type {HTMLElement} */ (e.target).closest('[data-tab-id]')
  );
  if (!tabEl) return;
  e.preventDefault();
  onClick(/** @type {any} */ (e));
}

// ── Drag to reorder ───────────────────────────────────────
// Native HTML5 drag-and-drop, like js/group-flyout/flyout-drag.js. The browser
// draws the drag image, so this file never positions anything; the only
// measurement is the comparison in `dropTarget`, and both of its inputs are
// converted to authored px first (see the header comment).

/** @param {DragEvent} e */
function onDragStart(e) {
  const tabEl = /** @type {HTMLElement | null} */ (
    /** @type {HTMLElement} */ (e.target).closest('[data-tab-id]')
  );
  if (!tabEl) return;
  const id = tabEl.dataset.tabId || '';
  if (!id) return;
  tabEl.classList.add('dragging');
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('application/x-server-tab', id);
  }
}

/** @param {DragEvent} e */
function onDragOver(e) {
  if (!e.dataTransfer) return;
  if (![...e.dataTransfer.types].includes('application/x-server-tab')) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';

  const host = stripEl();
  if (!host) return;
  const tabEl = /** @type {HTMLElement | null} */ (
    /** @type {HTMLElement} */ (e.target).closest('[data-tab-id]')
  );
  const all = [...host.querySelectorAll('[data-tab-id]')];
  if (!tabEl) {
    // Past the last tab (the `+` button's lane) — drop at the end.
    dropBefore = '';
  } else {
    const next = all[all.indexOf(tabEl) + 1];
    dropBefore = dropTarget(
      // Both in authored px. A raw clientX against a raw rect would also be
      // self-consistent, but converting keeps every number in this file in the
      // one space the app writes in, so a later reader cannot pick the wrong one.
      toInnerPx(e.clientX),
      innerRect(tabEl),
      tabEl.dataset.tabId || '',
      next ? /** @type {HTMLElement} */ (next).dataset.tabId || '' : '',
    );
  }
  for (const el of all) {
    /** @type {HTMLElement} */ (el).classList
      .toggle('drop-before', /** @type {HTMLElement} */ (el).dataset.tabId === dropBefore);
  }
  host.classList.toggle('drop-last', dropBefore === '');
}

/** @param {DragEvent} e */
function onDrop(e) {
  if (!e.dataTransfer) return;
  const id = e.dataTransfer.getData('application/x-server-tab');
  clearDragMarks();
  if (!id) return;
  e.preventDefault();
  moveTabBefore(id, dropBefore);
  dropBefore = '';
}

function clearDragMarks() {
  const host = stripEl();
  if (!host) return;
  host.classList.remove('drop-last');
  for (const el of host.querySelectorAll('[data-tab-id]')) {
    el.classList.remove('dragging', 'drop-before');
  }
}

// ── Lifecycle ─────────────────────────────────────────────

/**
 * Bind the strip. Called once, from app-init.js's mountPanels().
 *
 * Wiring only — no data. Panels mount before preferences are loaded, the same
 * reason the persisted collapse state and column widths are applied from
 * `bootstrapData()` rather than at mount time. The effect below is harmless
 * until then: the signal starts with no tabs at all, which `shouldShowTabs`
 * hides.
 */
export function initServerTabs() {
  const host = stripEl();
  if (!host) return;
  host.addEventListener('click', onClick);
  host.addEventListener('keydown', onKeydown);
  host.addEventListener('dragstart', /** @type {any} */ (onDragStart));
  host.addEventListener('dragover', /** @type {any} */ (onDragOver));
  host.addEventListener('drop', /** @type {any} */ (onDrop));
  host.addEventListener('dragend', clearDragMarks);
  host.addEventListener('dragleave', (e) => {
    const related = /** @type {Node | null} */ (/** @type {DragEvent} */ (e).relatedTarget);
    if (!related || !host.contains(related)) clearDragMarks();
  });

  // The `+` menu is on document.body, so its listeners are too. Capture on the
  // outside click, or a handler that stops propagation somewhere in the page
  // would leave the menu open over content it no longer describes.
  document.addEventListener('mousedown', (e) => {
    if (!menuEl) return;
    const t = /** @type {Node} */ (e.target);
    if (menuEl.contains(t) || host.contains(t)) return;
    closeMenu();
  }, true);
  document.addEventListener('keydown', onMenuKeydown);
  // The strip scrolls sideways and the window resizes; either moves the `+` out
  // from under a menu positioned against it. Re-anchoring on every scroll frame
  // would be the other answer, and a menu that follows a strip the user is
  // scrolling is not what they are asking for.
  host.addEventListener('scroll', closeMenu, { passive: true });
  window.addEventListener('resize', closeMenu);

  effect(render);
}

/**
 * Fill the strip. Called from app-init.js's bootstrapData(), after preferences
 * have loaded and the client-shell bridge is up.
 *
 * Renders nothing at all when there is no transport for
 * `PUT /v1/sources/active`: a strip whose tabs cannot switch is worse than no
 * strip, and this is the honest reading of a hub that predates the sources API.
 * The Preferences roster stays available either way — and says the same thing
 * out loud, because its own `Use` button goes through the same call.
 * @returns {Promise<void>}
 */
export async function startServerTabs() {
  if (!apiSupports('set_active_source')) {
    AppLog.info('server-tabs: this server has no /v1/sources — switch servers from Preferences');
    const host = stripEl();
    if (host) host.hidden = true;
    return;
  }
  // Paint from preferences first so the tabs exist on the first frame, then let
  // the hub's answer — the only thing that knows reachability — replace it. The
  // reverse order would leave the strip empty for a round trip on every launch.
  hydrateSourcesFromPreferences();
  const tab = getActiveTab();
  // Restore the active tab's own view, without a render: loadInventory() is
  // about to draw the grid anyway, and this runs after the inventory_view
  // hydration in app-init.js so the tab's more specific snapshot wins.
  if (tab && tab.view) restoreView(tab.view, { render: false });
  await refreshServerTabs();
}

/** Re-read the roster from the hub. Safe to call when the route does not exist. */
export function refreshServerTabs() {
  if (!apiSupports('list_sources')) return Promise.resolve();
  return loadSources().then(
    () => {},
    (e) => AppLog.warn('server-tabs: loading sources failed: ' + e.message),
  );
}
