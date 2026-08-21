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
import * as planStore from './cart-plan-store.js';
import {
  PRESETS, PRESET_LABELS, PRESET_TITLES,
  money, unitPrice, qty as fmtQty, derivation, note, candidateLabel,
  runnerUpDelta, summary, linesByRef, parseBoardCount,
} from './cart-plan-logic.js';
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
/** @type {HTMLInputElement|null} */
let boardsInputEl = null;
/** @type {HTMLElement|null} */
let presetSegEl = null;
/** @type {HTMLElement|null} */
let totalsEl = null;
/** Last value published by cartPlanSignal — read by renderTotals(), which runs
 *  from the cartsSignal effect too and so cannot read the signal itself
 *  without subscribing that effect to plan changes as well. */
let cartPlanState = /** @type {{plan: any|null, loading: boolean, error: string}} */
  ({ plan: null, loading: false, error: '' });

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
 * A part's AVAILABLE distributors are the ones it can actually be sourced
 * from. The backend attaches `available_distributors` to each cart item
 * (see domain/api_cart.py `_enrich_available`) = the union of the part's
 * record PNs AND its purchase-ledger PNs — matching what split/consolidate/
 * export resolve — so ledger-only distributors are offered too. We prefer
 * that server list when present.
 *
 * Fallbacks when the server field is absent (e.g. an older payload or a test
 * mock that doesn't provide it): for a `raw` item (no part_id) offer all four
 * (a "no options" dead-end select is worse); otherwise derive from the
 * resolved InventoryItem's non-empty record PN fields.
 * @param {any} cartItem
 * @param {Map<string, any>} invIndex
 * @returns {string[]}
 */
function availableDistributors(cartItem, invIndex) {
  if (cartItem.part_id && Array.isArray(cartItem.available_distributors)) {
    return cartItem.available_distributors.slice();
  }
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

// ── Purchase plan: board count, presets, per-row selection ────────────────

/**
 * Build a segmented control: buttons carrying `data-preset`, no listeners.
 *
 * Listener-free so both call sites can delegate — the row control is rebuilt
 * for every row on every render, and attaching four listeners per row would
 * mean hundreds of them on a real BOM. Matches how the row distributor
 * `<select>` is wired (see wireDistSelects).
 *
 * `inherited` marks a row with no preset of its own, following the cart
 * default. It renders as an underline rather than a filled segment because
 * "pinned to Min" and "following a cart that happens to be Min" are different
 * states, and conflating them makes pinning invisible.
 *
 * @param {string} active
 * @param {{inherited?: boolean, presets?: string[]}} [opts]
 * @returns {HTMLElement}
 */
function buildSegmented(active, opts = {}) {
  const presets = opts.presets || PRESETS;
  const seg = el('div', {
    class: 'cart-seg' + (opts.inherited ? ' is-inherited' : ''),
    role: 'group',
  });
  for (const preset of presets) {
    seg.appendChild(el('button', {
      type: 'button',
      'data-preset': preset,
      'aria-pressed': preset === active ? 'true' : 'false',
      title: PRESET_TITLES[preset] || preset,
    }, PRESET_LABELS[preset] || preset));
  }
  return seg;
}

async function handleBoardsChange() {
  const cart = cartStore.getActiveCart();
  if (!cart || !boardsInputEl) return;
  const parsed = parseBoardCount(boardsInputEl.value);
  if (parsed === null) {
    // Refused, not corrected: this number multiplies every per-board quantity
    // in the cart, so substituting one silently changes what gets ordered.
    boardsInputEl.classList.add('invalid');
    showToast('Board count must be a whole number of 1 or more');
    return;
  }
  boardsInputEl.classList.remove('invalid');
  if (parsed === cart.board_count) return;
  try {
    await cartStore.setBoardCount(parsed, cart.id);
  } catch (e) {
    AppLog.error('cart-modal: setBoardCount failed: ' + e.message);
  }
}

function handleBoardsStep(delta) {
  if (!boardsInputEl) return;
  const current = parseBoardCount(boardsInputEl.value) ?? 1;
  boardsInputEl.value = String(Math.max(1, current + delta));
  handleBoardsChange();
}

async function handleCartPresetChange(preset) {
  try {
    await planStore.setPreset(preset);
  } catch (e) {
    AppLog.error('cart-modal: setPreset failed: ' + e.message);
  }
}

/**
 * Pin (or unpin) one row's preset.
 *
 * Clicking the row's already-active preset clears it back to following the
 * cart default — the same control both pins and releases, so there is no
 * separate "unpin" affordance to discover.
 * @param {any} cartItem
 * @param {string} preset
 */
/**
 * Write one row's recommended quantity and packaging onto the cart line.
 *
 * The preset is left alone: the row asked for "the cheapest reel", the plan
 * answered, and accepting the answer does not turn the rule into a pinned
 * number. Raise the board count later and the row re-derives, which is the
 * whole point of storing a rule rather than a quantity.
 * @param {any} cartItem
 * @param {any} selected
 */
async function handleAcceptRow(cartItem, selected) {
  const cartId = cartStore.getActiveCartId();
  if (!cartId || !selected) return;
  try {
    await cartStore.updateItem(cartItem.ref, {
      qty: selected.qty,
      targetPackaging: selected.packaging || '',
      targetDistributor: selected.distributor || null,
    }, cartId);
  } catch (e) {
    AppLog.error('cart-modal: accept recommendation failed: ' + e.message);
  }
}

/**
 * Apply every row's recommendation at once.
 *
 * Sequential rather than parallel: each update reloads the carts list, and
 * racing a hundred of those against each other means the last response to
 * land decides what the UI believes.
 */
async function handleAcceptAll() {
  const cart = cartStore.getActiveCart();
  const plan = planStore.getPlan();
  if (!cart || !plan) return;
  const byRef = linesByRef(plan);
  const pending = (cart.items || [])
    .map((item) => ({ item, sel: (byRef.get(item.ref) || {}).selected }))
    .filter(({ item, sel }) => sel && (Number(item.qty) !== Number(sel.qty)
      || (item.target_packaging || '') !== (sel.packaging || '')));
  if (!pending.length) {
    showToast('Every line already matches the plan');
    return;
  }
  for (const { item, sel } of pending) {
    await handleAcceptRow(item, sel);
  }
  showToast(`Applied the plan to ${pending.length} line${pending.length === 1 ? '' : 's'}`);
}

async function handleRowPresetChange(cartItem, preset) {
  const cartId = cartStore.getActiveCartId();
  if (!cartId) return;
  const next = cartItem.preset === preset ? '' : preset;
  try {
    await cartStore.updateItem(cartItem.ref, { preset: next }, cartId);
  } catch (e) {
    AppLog.error('cart-modal: updateItem (preset) failed: ' + e.message);
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
 * Close the export menu when the user clicks anywhere outside it and its
 * toggle button. No-op while the menu is already hidden, so it never
 * interferes with the rest of the modal. The click that OPENS the menu
 * targets `.cart-export` (the toggle), which is excluded here, so opening
 * doesn't immediately re-close.
 * @param {MouseEvent} e
 */
function onDocClickCloseExport(e) {
  if (!exportMenuEl || exportMenuEl.classList.contains('hidden')) return;
  const t = /** @type {Node} */ (e.target);
  if (exportMenuEl.contains(t) || (t instanceof Element && t.closest('.cart-export'))) return;
  closeExportMenu();
}

/**
 * Close the export menu on Escape (without also closing the modal on the
 * same keystroke — we stop propagation only when the menu was actually open).
 * @param {KeyboardEvent} e
 */
function onKeydownCloseExport(e) {
  if (e.key !== 'Escape') return;
  if (!exportMenuEl || exportMenuEl.classList.contains('hidden')) return;
  e.stopPropagation();
  closeExportMenu();
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

async function handleNewCart() {
  try {
    const created = await cartStore.createCart(cartStore.prefillName());
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
      // Required quantity, with its derivation on the title. This is why
      // board_count is stored rather than folded into the number: without it
      // a row shows 5,000 and nobody can reconstruct where it came from.
      { key: '_need', label: 'Need', headerClass: 'cart-plan-need',
        cellClass: 'cart-plan-need',
        render: (item) => {
          const line = item._line;
          if (!line) return '—';
          const cell = el('span', {}, fmtQty(line.required_qty));
          const why = derivation(line);
          if (why) cell.setAttribute('title', why);
          return cell;
        } },
      // Per-row preset. Rendered by render() (not DataGrid's click-to-edit
      // text cell) because it is a set of buttons, and delegated below so it
      // survives the re-render every mutation triggers.
      { key: '_preset', label: 'Rule', headerClass: 'cart-plan-preset',
        cellClass: 'cart-plan-preset',
        render: (item) => buildSegmented(
          (item._line && item._line.preset) || planStore.getPreset(),
          { inherited: !item.preset },
        ) },
      // The quantity that will actually be ordered. Stays the stored, editable
      // number and the authoritative one — cart_export.py reads it, so a
      // column showing the *recommendation* here would put the screen and the
      // exported order out of step.
      { key: 'qty', label: 'Qty', width: '110px', align: 'right', mono: true,
        cellClass: 'cart-qty-cell',
        render: (item) => el('input', {
          type: 'number', min: '0', step: '1', class: 'cart-qty-input',
          value: String(item.qty ?? 0),
          title: 'Quantity to order. Typing one pins this row to Custom.',
        }) },
      // What the plan recommends. A button while it disagrees with the stored
      // quantity — accepting a recommendation is an explicit act, so a
      // re-plan after a price refresh can never quietly change an order.
      { key: '_buy', label: 'Plan says', headerClass: 'cart-plan-buy',
        cellClass: 'cart-plan-buy',
        render: (item) => {
          const line = item._line;
          // A custom row is priced against ONE packaging, so it gets a picker
          // rather than a recommendation — the plan has nothing to recommend
          // once the quantity is the user's own.
          if (item.preset === 'custom') return buildPackagingSelect(item, line);
          if (!line || !line.selected) return el('span', { class: 'cart-plan-note' }, '—');
          const label = candidateLabel(line.selected);
          const alt = runnerUpDelta(line);
          const agrees = Number(item.qty) === Number(line.selected.qty)
            && (item.target_packaging || '') === (line.selected.packaging || '');
          if (agrees) {
            const cell = el('span', { class: 'cart-plan-agrees' }, '✓ ' + label);
            if (alt) cell.setAttribute('title', alt.label);
            return cell;
          }
          const btn = el('button', {
            type: 'button', class: 'btn-sm cart-plan-accept',
            title: alt ? `Apply this quantity. ${alt.label}` : 'Apply this quantity',
          }, label);
          return btn;
        } },
      { key: '_unit', label: 'Unit', headerClass: 'cart-plan-unit',
        cellClass: 'cart-plan-unit',
        render: (item) => {
          const sel = item._line && item._line.selected;
          if (!sel) return '—';
          const cell = el('span', {}, unitPrice(sel.unit_price));
          cell.setAttribute('title', sel.on_break
            ? `price break at ${fmtQty(sel.break_qty)}`
            : `priced at the ${fmtQty(sel.break_qty)} break`);
          return cell;
        } },
      { key: '_spend', label: 'Spend', headerClass: 'cart-plan-spend',
        cellClass: 'cart-plan-spend',
        render: (item) => {
          const line = item._line;
          if (!line || !line.selected) return '';
          const fee = Number(line.selected.fee) || 0;
          const cell = el('span', {}, money(line.selected.spend));
          if (fee) cell.setAttribute('title', `includes a ${money(fee)} handling fee`);
          return cell;
        } },
      { key: '_note', label: '', render: (item) => {
        const text = note(item._line);
        if (!text) return '';
        const warn = !!(item._line && item._line.required_qty > 0 && !item._line.selected);
        return el('span', {
          class: 'cart-plan-note' + (warn ? ' is-warning' : ''), title: text,
        }, text);
      } },
      { key: '_dist', label: 'Target distributor', width: '150px',
        render: (item) => {
          // Always include the currently-set target as an option even if it's
          // no longer in the sourceable set, so a previously-chosen distributor
          // stays visibly selected rather than silently snapping to '—'.
          const opts = item._availableDist.slice();
          if (item.target_distributor && !opts.includes(item.target_distributor)) {
            opts.push(item.target_distributor);
          }
          const select = /** @type {HTMLSelectElement} */ (el('select', { class: 'cart-row-dist-select' },
            el('option', { value: '' }, '—'),
            ...opts.map((d) => el('option', {
              value: d, selected: item.target_distributor === d ? true : undefined,
            }, DISTRIBUTOR_LABELS[d] || d)),
          ));
          if (!item.target_distributor) select.value = '';
          return select;
        } },
    ],
    rowKey: (item) => item.ref,
    getRowClass: (item) => {
      const line = item._line;
      if (!line) return 'cart-row';
      if (line.required_qty === 0) return 'cart-row cart-row-covered';
      if (!line.selected) return 'cart-row cart-row-unpriced';
      return 'cart-row';
    },
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
    // Typing a quantity IS choosing a custom one, so the row pins itself
    // rather than keeping a rule it no longer follows — otherwise every future
    // re-plan shows a recommendation the row silently disagrees with.
    //
    // The packaging comes along because a custom quantity needs one to be
    // priced (several packagings quote the same quantity at different money),
    // and the one already on screen is what the user was looking at when they
    // typed. Where nothing is selected the packaging is left unset and the
    // row asks for one.
    const selected = item._line && item._line.selected;
    const patch = { qty, preset: 'custom' };
    if (selected && selected.packaging) patch.targetPackaging = selected.packaging;
    input.disabled = true;
    cartStore.updateItem(item.ref, patch, cart.id)
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

/**
 * Packaging picker for a custom row: the distinct packagings this part's own
 * candidates were drawn from, so the list only ever offers ladders that exist.
 * @param {any} item
 * @param {any} line
 * @returns {HTMLElement}
 */
function buildPackagingSelect(item, line) {
  const seen = [];
  for (const candidate of (line && line.candidates) || []) {
    const name = candidate.packaging || '';
    if (name && !seen.includes(name)) seen.push(name);
  }
  const current = item.target_packaging || '';
  // Keep a packaging that is no longer on offer visibly selected rather than
  // snapping to "—" (same reasoning as the target-distributor select).
  if (current && !seen.includes(current)) seen.push(current);
  if (!seen.length) return el('span', { class: 'cart-plan-note is-warning' }, 'no priced packaging');
  const select = /** @type {HTMLSelectElement} */ (el('select', {
    class: 'cart-row-pkg-select', title: 'Packaging this custom quantity is priced against',
  },
  el('option', { value: '' }, '—'),
  ...seen.map((name) => el('option', {
    value: name, selected: name === current ? true : undefined,
  }, name)),
  ));
  if (!current) select.value = '';
  return select;
}

/**
 * Per-row packaging changes, delegated like the distributor select.
 * @param {HTMLElement} container
 */
function wirePackagingSelects(container) {
  container.addEventListener('change', (ev) => {
    const select = ev.target;
    if (!(select instanceof HTMLSelectElement)
      || !select.classList.contains('cart-row-pkg-select')) return;
    const tr = select.closest('tr[data-row-key]');
    if (!tr) return;
    const ref = /** @type {HTMLElement} */ (tr).dataset.rowKey;
    const cartId = cartStore.getActiveCartId();
    if (!cartId) return;
    cartStore.updateItem(ref, { targetPackaging: select.value }, cartId)
      .catch((e) => AppLog.error('cart-modal: updateItem (packaging) failed: ' + e.message));
  });
}

/**
 * The preset a click landed on, or null if it missed a segment.
 * @param {Event} ev
 * @returns {string|null}
 */
function presetFromEvent(ev) {
  const target = ev.target;
  if (!(target instanceof HTMLElement)) return null;
  const btn = target.closest('button[data-preset]');
  return btn instanceof HTMLElement ? (btn.dataset.preset || null) : null;
}

/**
 * Per-row "apply this quantity" clicks, delegated like the preset segments.
 * @param {HTMLElement} container
 */
function wireAcceptButtons(container) {
  container.addEventListener('click', (ev) => {
    const target = ev.target;
    if (!(target instanceof HTMLElement)) return;
    const btn = target.closest('button.cart-plan-accept');
    if (!btn) return;
    const tr = btn.closest('tr[data-row-key]');
    if (!tr) return;
    const ref = /** @type {HTMLElement} */ (tr).dataset.rowKey;
    const cart = cartStore.getActiveCart();
    const item = cart && cart.items.find((it) => it.ref === ref);
    const line = linesByRef(planStore.getPlan()).get(ref);
    if (!item || !line) return;
    handleAcceptRow(item, line.selected);
  });
}

/**
 * Per-row preset clicks, delegated on the grid container so they survive the
 * re-render every cart mutation triggers.
 * @param {HTMLElement} container
 */
function wirePresetSegments(container) {
  container.addEventListener('click', (ev) => {
    const preset = presetFromEvent(ev);
    if (!preset) return;
    const target = /** @type {HTMLElement} */ (ev.target);
    const tr = target.closest('tr[data-row-key]');
    if (!tr) return;
    const ref = /** @type {HTMLElement} */ (tr).dataset.rowKey;
    const cart = cartStore.getActiveCart();
    const item = cart && cart.items.find((it) => it.ref === ref);
    if (!item) return;
    handleRowPresetChange(item, preset);
  });
}

// ── Modal shell (built once, dynamically — no static index.html markup) ────

function buildModalDom() {
  if (document.getElementById('cart-modal')) return;

  gridWrapEl = el('div', { class: 'cart-table-wrap', id: 'cart-table-wrap' });
  buildGrid(gridWrapEl);
  wireQtyInputs(gridWrapEl);
  wireDistSelects(gridWrapEl);
  wirePresetSegments(gridWrapEl);
  wireAcceptButtons(gridWrapEl);
  wirePackagingSelects(gridWrapEl);

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

  // Dismiss the export menu on an outside click or Escape. Registered once
  // (buildModalDom is guarded to run a single time); both handlers no-op while
  // the menu is hidden, so they don't touch the rest of the modal. Escape uses
  // capture + stopPropagation so it closes the menu before the modal's own
  // Escape-to-close handler sees the keystroke.
  document.addEventListener('click', onDocClickCloseExport);
  document.addEventListener('keydown', onKeydownCloseExport, true);

  // Board count: the multiplier behind every per-board quantity. Committed on
  // 'change' (blur or Enter) rather than 'input' — every keystroke of "25"
  // passes through 2, and refetching a plan for 2 boards on the way to 25 is
  // both wasted and briefly wrong on screen.
  boardsInputEl = /** @type {HTMLInputElement} */ (el('input', {
    type: 'number', min: '1', step: '1', class: 'cart-boards-input',
    id: 'cart-boards-input', value: '1',
    'aria-label': 'Number of boards this cart builds',
  }));
  boardsInputEl.addEventListener('change', handleBoardsChange);
  const boardsDown = el('button', { type: 'button', class: 'btn-sm', 'aria-label': 'Fewer boards' }, '−');
  boardsDown.addEventListener('click', () => handleBoardsStep(-1));
  const boardsUp = el('button', { type: 'button', class: 'btn-sm', 'aria-label': 'More boards' }, '+');
  boardsUp.addEventListener('click', () => handleBoardsStep(1));
  const boardsGroup = el('div', { class: 'cart-topbar-group cart-boards' },
    el('span', { class: 'cart-boards-label' }, 'Boards'),
    boardsDown, boardsInputEl, boardsUp,
  );

  // Cart-wide default rule. Custom is deliberately absent here: it means "a
  // quantity you typed", which is a per-row fact and meaningless as a default.
  presetSegEl = buildSegmented(planStore.getPreset(), {
    presets: PRESETS.filter((preset) => preset !== 'custom'),
  });
  presetSegEl.addEventListener('click', (ev) => {
    const preset = presetFromEvent(ev);
    if (preset) handleCartPresetChange(preset);
  });
  const applyAllBtn = el('button', {
    type: 'button', class: 'btn-sm cart-plan-apply-all',
    title: 'Set every line\u2019s quantity to what the plan recommends',
  }, 'Apply plan');
  applyAllBtn.addEventListener('click', handleAcceptAll);

  const presetGroup = el('div', { class: 'cart-topbar-group' },
    el('span', { class: 'cart-boards-label' }, 'Rule'), presetSegEl, applyAllBtn,
  );

  totalsEl = el('div', { class: 'cart-plan-totals' });

  const topbar = el('div', { class: 'cart-topbar' },
    switcherEl, newBtn, renameBtn, deleteBtn, clearBtn,
    boardsGroup, presetGroup,
    splitGroup, consolidateGroup, exportGroup,
  );

  titleEl = el('div', { class: 'modal-title', id: 'cart-modal-title' }, 'Cart');

  const closeBtn = el('button', {
    type: 'button', class: 'btn-md btn-cancel', id: 'cart-modal-close',
  }, 'Close');

  const head = el('div', { class: 'cart-modal-head' }, titleEl, closeBtn);

  const modalInner = el('div', { class: 'modal cart-modal' },
    head, topbar, emptyStateEl, gridWrapEl, totalsEl);
  const overlay = el('div', { class: 'modal-overlay hidden', id: 'cart-modal' }, modalInner);
  document.body.appendChild(overlay);

  cartModal = Modal('cart-modal', { cancelId: 'cart-modal-close' });

  // cartsSignal is the sole re-render path for cart state (see js/signals.js
  // docblock) — re-renders the grid/title/empty-state on every cart mutation
  // from any client, matching the header badge's own effect() subscription.
  //
  // A cart mutation also invalidates the plan: adding a line, changing a
  // board count or pinning a preset all change what should be bought. The
  // refetch is scheduled (debounced) rather than immediate so a burst of
  // mutations costs one request.
  effect(() => {
    cartsSignal.get();
    renderFromActiveCart();
    planStore.schedulePlanRefresh();
  });

  // The plan arriving re-renders the grid but must NOT trigger another
  // refetch — reading cartsSignal here would make the two effects feed each
  // other forever.
  effect(() => {
    cartPlanState = planStore.cartPlanSignal.get();
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

  if (boardsInputEl && document.activeElement !== boardsInputEl) {
    // Never overwrite what the user is mid-way through typing.
    boardsInputEl.value = String(cart.board_count ?? 1);
  }

  syncPresetSeg();

  const invIndex = buildInventoryIndex();
  const byRef = linesByRef(planStore.getPlan());
  const rows = (cart.items || []).map((item) => ({
    ...item,
    _display: resolveDisplay(item, invIndex),
    _availableDist: availableDistributors(item, invIndex),
    _line: byRef.get(item.ref) || null,
  }));
  grid.render(rows);
  renderTotals();
}

/**
 * Reflect the cart-wide preset back onto the topbar control.
 *
 * The topbar segment is built once (it is not inside the grid, so nothing
 * rebuilds it), which means its pressed state has to be pushed rather than
 * re-rendered.
 */
function syncPresetSeg() {
  if (!presetSegEl) return;
  const active = planStore.getPreset();
  for (const btn of presetSegEl.querySelectorAll('button[data-preset]')) {
    btn.setAttribute('aria-pressed', btn.getAttribute('data-preset') === active ? 'true' : 'false');
  }
}

/**
 * Cart-level totals strip.
 *
 * `covered by stock` and `unpriced` are shown beside the total, not folded
 * into it: a total that silently omits rows nobody could price reads as
 * complete when it is not.
 */
function renderTotals() {
  if (!totalsEl) return;
  const { plan, loading, error } = cartPlanState;
  totalsEl.replaceChildren();
  totalsEl.classList.toggle('is-loading', !!loading);
  if (!plan) {
    if (loading) totalsEl.appendChild(el('span', { class: 'cart-plan-total-label' }, 'Pricing…'));
    return;
  }
  const s = summary(plan);
  totalsEl.append(
    el('span', { class: 'cart-plan-total-label' }, 'Plan total'),
    el('span', { class: 'cart-plan-total-spend' }, s.spend),
    el('span', { class: 'cart-plan-total-label' },
      `${s.lines} line${s.lines === 1 ? '' : 's'} · ${plan.board_count} board${plan.board_count === 1 ? '' : 's'}`),
  );
  if (s.caveat) totalsEl.appendChild(el('span', { class: 'cart-plan-caveat' }, s.caveat));
  // A failed refresh keeps the previous numbers on screen and labels them
  // stale — a total that is out of date and says so beats no total at all.
  if (error) totalsEl.appendChild(el('span', { class: 'cart-plan-stale' }, error));
}

// ── Public API ───────────────────────────────────────────────────────────

/**
 * Open the cart modal — replaces the js/cart/cart-header.js stub.
 */
export function openCartModal() {
  buildModalDom();
  renderFromActiveCart();
  cartModal.open();
  // Fetch immediately rather than waiting on the debounce — the modal opening
  // is the one moment the user is definitely looking at the numbers.
  planStore.loadPlan().catch((e) => AppLog.error('cart-modal: loadPlan failed: ' + e.message));
}
