// @ts-check
/* cart-modal.js — Cart modal: inventory-view-style DataGrid of the active
   cart's line items with a top button bar (editable qty, delete line, clear
   cart — Task B6). Later tasks (B7 cart management, B8 target-distributor
   editing/split-consolidate, B9 export) extend the SAME modal/top bar rather
   than building a new one — see cart-topbar's layout and the TODOs below.

   Built dynamically (no static index.html markup) — same pattern as
   js/feeders-modal.js: a single `.modal-overlay` + `.modal` appended to
   <body> once, wired through js/ui-helpers.js's Modal() factory.

   Display fields (description/mpn/package/on-hand) are NOT stored on cart
   items — items only carry {ref, part_id, raw, qty, target_distributor}. They
   are resolved here by joining part_id against the loaded inventory
   (store.inventory) via invPartKey (js/part-keys.js); raw (no part_id) items
   fall back to raw.mpn/raw.description with on-hand read as "—" (not in
   inventory). */

import { showToast, Modal } from '../ui-helpers.js';
import { AppLog } from '../api.js';
import { store } from '../store.js';
import { invPartKey } from '../part-keys.js';
import { el } from '../dom/html.js';
import { DataGrid } from '../components/data-grid.js';
import { cartsSignal, effect } from '../signals.js';
import * as cartStore from './cart-store.js';
import { downloadCsv, copyPaste } from './cart-export.js';

/** @type {{el:HTMLTableElement, render(data:any[]):void, refresh():void, getData():any[], destroy():void}|null} */
let grid = null;
/** @type {{open():void, close():void, el:HTMLElement}|null} */
let cartModal = null;
/** @type {HTMLElement|null} */
let titleEl = null;
/** @type {HTMLElement|null} */
let emptyStateEl = null;
/** @type {HTMLElement|null} */
let gridWrapEl = null;
/** @type {HTMLSelectElement|null} */
let switcherEl = null;
/** @type {HTMLSelectElement|null} */
let splitDistEl = null;
/** @type {HTMLInputElement|null} */
let splitRemoveEl = null;
/** @type {HTMLSelectElement|null} */
let consolidateDistEl = null;
/** @type {HTMLElement|null} */
let exportMenuEl = null;

// All distributors the app knows how to source from (fixed order/labels used
// by the per-row select's fallback for raw items and by the top-bar
// split/consolidate selects, which aren't scoped to one line).
const ALL_DISTRIBUTORS = ['lcsc', 'digikey', 'mouser', 'pololu'];
const DISTRIBUTOR_LABELS = { lcsc: 'LCSC', digikey: 'DigiKey', mouser: 'Mouser', pololu: 'Pololu' };

// ── Display-field resolution ─────────────────────────────────────────────

/**
 * Build the inventory lookup (part_id -> InventoryItem) fresh each render —
 * store.inventory can change between opens/renders (SSE-driven refresh), so
 * this is not cached across calls.
 * @returns {Map<string, any>}
 */
function buildInventoryIndex() {
  const idx = new Map();
  for (const item of store.inventory) {
    const key = invPartKey(item);
    if (key) idx.set(key, item);
  }
  return idx;
}

/**
 * @param {any} cartItem
 * @param {Map<string, any>} invIndex
 * @returns {{ label: string, pkg: string, onHand: string }}
 */
function resolveDisplay(cartItem, invIndex) {
  if (cartItem.part_id) {
    const inv = invIndex.get(cartItem.part_id);
    if (inv) {
      return {
        label: inv.description || cartItem.part_id,
        pkg: inv.package || '—',
        onHand: String(inv.qty ?? '—'),
      };
    }
    // part_id set but not found in the currently-loaded inventory.
    return { label: cartItem.part_id + ' (unresolved)', pkg: '—', onHand: '—' };
  }
  const raw = cartItem.raw || {};
  return {
    label: raw.description || raw.mpn || '(unknown part)',
    pkg: raw.package || '—',
    onHand: '—',
  };
}

/**
 * A part's AVAILABLE distributors = the subset of {lcsc,digikey,mouser,pololu}
 * whose PN field is non-empty on the resolved InventoryItem — derived
 * client-side from the already-loaded inventory, no backend round-trip. For
 * `raw` items (no part_id / no inventory match) there's no PN data to check,
 * so all four are offered (simpler than a "no options" dead-end select).
 * @param {any} cartItem
 * @param {Map<string, any>} invIndex
 * @returns {string[]}
 */
function availableDistributors(cartItem, invIndex) {
  const inv = cartItem.part_id ? invIndex.get(cartItem.part_id) : null;
  if (!inv) return ALL_DISTRIBUTORS.slice();
  return ALL_DISTRIBUTORS.filter((d) => (inv[d] || '').trim());
}

// ── Row actions ────────────────────────────────────────────────────────────

async function handleDeleteLine(cartItem) {
  const cartId = cartStore.getActiveCartId();
  if (!cartId) return;
  try {
    await cartStore.removeItem(cartItem.ref, cartId);
    showToast('Line removed');
  } catch (e) {
    AppLog.error('cart-modal: removeItem failed: ' + e.message);
  }
}

async function handleClearCart() {
  const cart = cartStore.getActiveCart();
  if (!cart) return;
  if (!window.confirm(`Clear all items from "${cart.name}"? This cannot be undone.`)) return;
  try {
    await cartStore.clearCart(cart.id);
    showToast('Cart cleared');
  } catch (e) {
    AppLog.error('cart-modal: clearCart failed: ' + e.message);
  }
}

/**
 * @param {any} cartItem
 * @param {string} value '' means unset (null)
 */
async function handleRowDistributorChange(cartItem, value) {
  const cartId = cartStore.getActiveCartId();
  if (!cartId) return;
  try {
    await cartStore.updateItem(cartItem.ref, { targetDistributor: value || null }, cartId);
  } catch (e) {
    AppLog.error('cart-modal: updateItem (targetDistributor) failed: ' + e.message);
  }
}

// ── Top bar: split by distributor / consolidate to distributor (Task B8) ──

async function handleSplitGo() {
  const cart = cartStore.getActiveCart();
  if (!cart || !splitDistEl) return;
  const distributor = splitDistEl.value;
  if (!distributor) {
    showToast('Choose a distributor to split by');
    return;
  }
  const removeFromSource = !!(splitRemoveEl && splitRemoveEl.checked);
  const newName = `${cart.name || 'Cart'} — ${DISTRIBUTOR_LABELS[distributor] || distributor}`;
  try {
    const result = await cartStore.splitCart(distributor, newName, removeFromSource, cart.id);
    const newCart = result && result.new;
    if (newCart && newCart.id) {
      await cartStore.setActiveCart(newCart.id);
    }
    showToast(`Split ${DISTRIBUTOR_LABELS[distributor] || distributor} lines into a new cart`);
  } catch (e) {
    AppLog.error('cart-modal: splitCart failed: ' + e.message);
  }
}

async function handleConsolidateGo() {
  const cart = cartStore.getActiveCart();
  if (!cart || !consolidateDistEl) return;
  const distributor = consolidateDistEl.value;
  if (!distributor) {
    showToast('Choose a distributor to consolidate to');
    return;
  }
  try {
    const result = await cartStore.consolidateCart(distributor, cart.id);
    const unresolved = (result && result.unresolved) || [];
    const label = DISTRIBUTOR_LABELS[distributor] || distributor;
    if (unresolved.length > 0) {
      showToast(`Consolidated to ${label}; ${unresolved.length} line(s) could not be resolved and were left unchanged`);
    } else {
      showToast(`Consolidated to ${label}`);
    }
  } catch (e) {
    AppLog.error('cart-modal: consolidateCart failed: ' + e.message);
  }
}

// ── Export (Task B9): download LCSC/DigiKey CSV, copy paste-format list ───

function handleExportToggle() {
  if (!exportMenuEl) return;
  exportMenuEl.classList.toggle('hidden');
}

function closeExportMenu() {
  if (exportMenuEl) exportMenuEl.classList.add('hidden');
}

/**
 * @param {'csv'|'paste'} fmt
 * @param {string} distributor
 */
async function handleExportAction(fmt, distributor) {
  const cartId = cartStore.getActiveCartId();
  if (!cartId) return;
  closeExportMenu();
  try {
    if (fmt === 'csv') {
      await downloadCsv(cartId, distributor);
    } else {
      await copyPaste(cartId, distributor);
    }
  } catch (e) {
    AppLog.error(`cart-modal: export (${fmt}/${distributor}) failed: ` + e.message);
  }
}

// ── Cart management (Task B7): switch / create / rename / delete ──────────

/**
 * "<YYYY-MM-DD> · <loadedBomFileName or ''>" — trims the separator when no
 * BOM is loaded. Reuses store.bomFileName (the same getter the BOM panel
 * reads), not a re-derivation.
 * @returns {string}
 */
function prefillName() {
  const today = new Date().toISOString().slice(0, 10);
  const bomName = store.bomFileName || '';
  return bomName ? `${today} · ${bomName}` : today;
}

async function handleNewCart() {
  try {
    const created = await cartStore.createCart(prefillName());
    // create_cart does NOT make the new cart active (mirrors carts.create()/
    // set_active() being separate backend calls) — do it explicitly so the
    // switcher reflects the cart the user just made.
    if (created && created.id) {
      await cartStore.setActiveCart(created.id);
    }
    showToast('Cart created');
  } catch (e) {
    AppLog.error('cart-modal: createCart failed: ' + e.message);
  }
}

async function handleRenameCart() {
  const cart = cartStore.getActiveCart();
  if (!cart) return;
  const name = window.prompt('Rename cart', cart.name || '');
  if (name === null) return; // cancelled
  const trimmed = name.trim();
  if (!trimmed) return;
  try {
    await cartStore.renameCart(cart.id, trimmed);
    showToast('Cart renamed');
  } catch (e) {
    AppLog.error('cart-modal: renameCart failed: ' + e.message);
  }
}

async function handleDeleteCart() {
  const cart = cartStore.getActiveCart();
  if (!cart) return;
  if (!window.confirm(`Delete cart "${cart.name}"? This cannot be undone.`)) return;
  try {
    await cartStore.deleteCart(cart.id);
    // deleteCart() awaits loadCarts() internally, so getCarts() here already
    // excludes the deleted cart — fall back to the first remaining one, or
    // leave no active cart (renderFromActiveCart shows the empty state).
    const remaining = cartStore.getCarts();
    if (remaining.length > 0) {
      await cartStore.setActiveCart(remaining[0].id);
    }
    showToast('Cart deleted');
  } catch (e) {
    AppLog.error('cart-modal: deleteCart failed: ' + e.message);
  }
}

function handleSwitchCart(ev) {
  const id = /** @type {HTMLSelectElement} */ (ev.target).value;
  if (!id) return;
  cartStore.setActiveCart(id).catch((e) => AppLog.error('cart-modal: setActiveCart failed: ' + e.message));
}

// ── DataGrid ──────────────────────────────────────────────────────────────

function buildGrid(container) {
  grid = DataGrid(container, {
    columns: [
      { key: '_part', label: 'Part', render: (item) => item._display.label },
      { key: '_pkg', label: 'Package', width: '110px', render: (item) => item._display.pkg },
      { key: '_onhand', label: 'On-hand', width: '90px', align: 'right', mono: true,
        render: (item) => item._display.onHand },
      { key: 'qty', label: 'Qty to purchase', width: '130px', align: 'right', mono: true,
        cellClass: 'cart-qty-cell',
        render: (item) => el('input', {
          type: 'number', min: '0', step: '1', class: 'cart-qty-input',
          value: String(item.qty ?? 0),
        }) },
      { key: '_dist', label: 'Target distributor', width: '150px',
        render: (item) => {
          const select = /** @type {HTMLSelectElement} */ (el('select', { class: 'cart-row-dist-select' },
            el('option', { value: '' }, '—'),
            ...item._availableDist.map((d) => el('option', {
              value: d, selected: item.target_distributor === d ? true : undefined,
            }, DISTRIBUTOR_LABELS[d] || d)),
          ));
          if (!item.target_distributor) select.value = '';
          return select;
        } },
    ],
    rowKey: (item) => item.ref,
    getRowClass: () => 'cart-row',
    rowActions: [
      { key: 'delete', label: '✕', class: 'cart-del-line', title: 'Remove this line',
        onClick: (item) => handleDeleteLine(item) },
    ],
    emptyMessage: 'No items — this cart is empty.',
    rovingNav: true,
  });
}

// Qty edits render via `render()` as a live <input class="cart-qty-input">
// (always editable, no click-to-activate step) rather than DataGrid's own
// onCellEdit/click-to-edit text cell — that's a better fit for a quantity
// field the user expects to type into directly. Committed on 'change'
// (blur or Enter), delegated on the grid container so it survives re-renders.
function wireQtyInputs(container) {
  container.addEventListener('change', (ev) => {
    const input = /** @type {HTMLElement} */ (ev.target);
    if (!(input instanceof HTMLInputElement) || !input.classList.contains('cart-qty-input')) return;
    const tr = input.closest('tr[data-row-key]');
    if (!tr) return;
    const ref = /** @type {HTMLElement} */ (tr).dataset.rowKey;
    const cart = cartStore.getActiveCart();
    const item = cart && cart.items.find((it) => it.ref === ref);
    if (!item) return;
    const raw = input.value.trim();
    const qty = Number(raw);
    if (raw === '' || !Number.isInteger(qty) || qty < 0) {
      AppLog.warn('cart-modal: rejected non-integer/negative qty input: ' + input.value);
      input.value = String(item.qty ?? 0);
      showToast('Quantity must be a whole number ≥ 0');
      return;
    }
    input.disabled = true;
    cartStore.updateItem(item.ref, { qty }, cart.id)
      .catch((e) => AppLog.error('cart-modal: updateItem (qty) failed: ' + e.message))
      .finally(() => { input.disabled = false; });
  });
}

// Per-row target-distributor <select> commit — delegated the same way as
// wireQtyInputs so it survives re-renders (rebuilt <select> elements on every
// renderFromActiveCart() call).
function wireDistSelects(container) {
  container.addEventListener('change', (ev) => {
    const select = /** @type {HTMLElement} */ (ev.target);
    if (!(select instanceof HTMLSelectElement) || !select.classList.contains('cart-row-dist-select')) return;
    const tr = select.closest('tr[data-row-key]');
    if (!tr) return;
    const ref = /** @type {HTMLElement} */ (tr).dataset.rowKey;
    const cart = cartStore.getActiveCart();
    const item = cart && cart.items.find((it) => it.ref === ref);
    if (!item) return;
    handleRowDistributorChange(item, select.value);
  });
}

// ── Modal shell (built once, dynamically — no static index.html markup) ────

function buildModalDom() {
  if (document.getElementById('cart-modal')) return;

  gridWrapEl = el('div', { class: 'cart-table-wrap', id: 'cart-table-wrap' });
  buildGrid(gridWrapEl);
  wireQtyInputs(gridWrapEl);
  wireDistSelects(gridWrapEl);

  emptyStateEl = el('div', { class: 'cart-empty-state hidden' }, 'No active cart — add a part to the cart to create one.');

  switcherEl = /** @type {HTMLSelectElement} */ (el('select', { class: 'cart-switcher', id: 'cart-switcher' }));
  switcherEl.addEventListener('change', handleSwitchCart);

  const newBtn = el('button', {
    type: 'button', class: 'btn-sm cart-new',
  }, 'New');
  newBtn.addEventListener('click', handleNewCart);

  const renameBtn = el('button', {
    type: 'button', class: 'btn-sm cart-rename',
  }, 'Rename');
  renameBtn.addEventListener('click', handleRenameCart);

  const deleteBtn = el('button', {
    type: 'button', class: 'btn-sm btn-danger cart-delete',
  }, 'Delete');
  deleteBtn.addEventListener('click', handleDeleteCart);

  const clearBtn = el('button', {
    type: 'button', class: 'btn-sm btn-danger', id: 'cart-clear-btn',
  }, 'Clear cart');
  clearBtn.addEventListener('click', handleClearCart);

  // Split by distributor (Task B8): pick a distributor, optionally remove
  // the moved lines from the source cart, then splitCart().
  splitDistEl = /** @type {HTMLSelectElement} */ (el('select', { class: 'cart-split-dist', title: 'Split by distributor' },
    el('option', { value: '' }, 'Split by…'),
    ...ALL_DISTRIBUTORS.map((d) => el('option', { value: d }, DISTRIBUTOR_LABELS[d])),
  ));
  const splitRemoveLabel = el('label', { class: 'cart-split-remove-label', title: 'Remove moved lines from this cart' });
  splitRemoveEl = /** @type {HTMLInputElement} */ (el('input', { type: 'checkbox', class: 'cart-split-remove' }));
  splitRemoveLabel.append(splitRemoveEl, ' Remove from this cart');
  const splitGoBtn = el('button', { type: 'button', class: 'btn-sm cart-split-go' }, 'Split');
  splitGoBtn.addEventListener('click', handleSplitGo);

  // Consolidate to distributor (Task B8): sets target_distributor on every
  // sourceable line via consolidateCart(); unresolved lines are left alone
  // and surfaced in the result toast.
  consolidateDistEl = /** @type {HTMLSelectElement} */ (el('select', { class: 'cart-consolidate-dist', title: 'Consolidate to distributor' },
    el('option', { value: '' }, 'Consolidate to…'),
    ...ALL_DISTRIBUTORS.map((d) => el('option', { value: d }, DISTRIBUTOR_LABELS[d])),
  ));
  const consolidateGoBtn = el('button', { type: 'button', class: 'btn-sm cart-consolidate-go' }, 'Consolidate');
  consolidateGoBtn.addEventListener('click', handleConsolidateGo);

  // Grouped so each op's select+button(+checkbox) wraps as a single unit at
  // narrow widths rather than splitting mid-control (Task B8 topbar-overflow
  // fix — see css/components/cart.css .cart-topbar/.cart-topbar-group).
  const splitGroup = el('div', { class: 'cart-topbar-group' }, splitDistEl, splitRemoveLabel, splitGoBtn);
  const consolidateGroup = el('div', { class: 'cart-topbar-group' }, consolidateDistEl, consolidateGoBtn);

  // Export (Task B9): a toggle button + a hidden-by-default menu of 4 actions
  // (2 distributors × CSV-download/copy-paste). Kept as a plain toggled
  // sibling (not an absolutely-positioned floating menu) so it participates
  // in the topbar's own wrap layout rather than needing separate
  // viewport-clipping handling.
  const exportBtn = el('button', { type: 'button', class: 'btn-sm cart-export' }, 'Export ▾');
  exportBtn.addEventListener('click', handleExportToggle);

  const lcscCsvBtn = el('button', { type: 'button', class: 'btn-sm cart-export-lcsc-csv' }, 'LCSC CSV');
  lcscCsvBtn.addEventListener('click', () => handleExportAction('csv', 'lcsc'));
  const digikeyCsvBtn = el('button', { type: 'button', class: 'btn-sm cart-export-digikey-csv' }, 'DigiKey CSV');
  digikeyCsvBtn.addEventListener('click', () => handleExportAction('csv', 'digikey'));
  const lcscPasteBtn = el('button', { type: 'button', class: 'btn-sm cart-export-lcsc-paste' }, 'Copy LCSC paste');
  lcscPasteBtn.addEventListener('click', () => handleExportAction('paste', 'lcsc'));
  const digikeyPasteBtn = el('button', { type: 'button', class: 'btn-sm cart-export-digikey-paste' }, 'Copy DigiKey paste');
  digikeyPasteBtn.addEventListener('click', () => handleExportAction('paste', 'digikey'));

  exportMenuEl = el('div', { class: 'cart-export-menu hidden' },
    lcscCsvBtn, digikeyCsvBtn, lcscPasteBtn, digikeyPasteBtn,
  );
  const exportGroup = el('div', { class: 'cart-topbar-group' }, exportBtn, exportMenuEl);

  const topbar = el('div', { class: 'cart-topbar' },
    switcherEl, newBtn, renameBtn, deleteBtn, clearBtn,
    splitGroup, consolidateGroup, exportGroup,
  );

  titleEl = el('div', { class: 'modal-title', id: 'cart-modal-title' }, 'Cart');

  const closeBtn = el('button', {
    type: 'button', class: 'btn-md btn-cancel', id: 'cart-modal-close',
  }, 'Close');

  const head = el('div', { class: 'cart-modal-head' }, titleEl, closeBtn);

  const modalInner = el('div', { class: 'modal cart-modal' }, head, topbar, emptyStateEl, gridWrapEl);
  const overlay = el('div', { class: 'modal-overlay hidden', id: 'cart-modal' }, modalInner);
  document.body.appendChild(overlay);

  cartModal = Modal('cart-modal', { cancelId: 'cart-modal-close' });

  // cartsSignal is the sole re-render path for cart state (see js/signals.js
  // docblock) — re-renders the grid/title/empty-state on every cart mutation
  // from any client, matching the header badge's own effect() subscription.
  effect(() => {
    cartsSignal.get();
    renderFromActiveCart();
  });
}

/** Rebuild the #cart-switcher <option>s from cartStore.getCarts(), selecting the active cart. */
function renderSwitcher() {
  if (!switcherEl) return;
  const carts = cartStore.getCarts();
  const activeId = cartStore.getActiveCartId();
  const options = carts.map((c) => el('option', { value: c.id, selected: c.id === activeId ? true : undefined }, c.name || c.id));
  switcherEl.replaceChildren(...options);
  const hasCarts = carts.length > 0;
  document.querySelector('.cart-rename')?.toggleAttribute('disabled', !hasCarts);
  document.querySelector('.cart-delete')?.toggleAttribute('disabled', !hasCarts);
}

function renderFromActiveCart() {
  if (!grid || !titleEl || !emptyStateEl || !gridWrapEl) return;
  renderSwitcher();
  const cart = cartStore.getActiveCart();
  if (!cart) {
    titleEl.textContent = 'Cart';
    emptyStateEl.classList.remove('hidden');
    gridWrapEl.classList.add('hidden');
    document.getElementById('cart-clear-btn')?.setAttribute('disabled', 'disabled');
    return;
  }
  titleEl.textContent = cart.name || 'Cart';
  emptyStateEl.classList.add('hidden');
  gridWrapEl.classList.remove('hidden');
  document.getElementById('cart-clear-btn')?.removeAttribute('disabled');

  const invIndex = buildInventoryIndex();
  const rows = (cart.items || []).map((item) => ({
    ...item,
    _display: resolveDisplay(item, invIndex),
    _availableDist: availableDistributors(item, invIndex),
  }));
  grid.render(rows);
}

// ── Public API ───────────────────────────────────────────────────────────

/**
 * Open the cart modal — replaces the js/cart/cart-header.js stub.
 */
export function openCartModal() {
  buildModalDom();
  renderFromActiveCart();
  cartModal.open();
}
