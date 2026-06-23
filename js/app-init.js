/* app-init.js — Application entry point: wires up modals, global shortcuts, loads inventory */

import { EventBus, Events } from './event-bus.js';
import { api, AppLog, whenPywebviewReady } from './api.js';
import { showToast, Modal, setEnterSubmitEnabled } from './ui-helpers.js';
import { UndoRedo } from './undo-redo.js';
import { store, loadPreferences, loadInventory, onInventoryUpdated, getShortcutPrefs } from './store.js';
import { processBOM } from './csv-parser.js';
import { matchBOM } from './matching.js';
import { colorizeRefs, REF_COLOR_MAP, invPartKey } from './part-keys.js';
import { openPreferencesModal, applyPreferences, wireDigikeyButtons } from './preferences-modal.js';
import { wireVendorsModal, openVendorsModal } from './vendors-modal.js';
import { initShortcuts } from './a11y/shortcuts.js';
import { initShortcutHelp } from './a11y/shortcut-help.js';
import { saveBomFile } from './bom/bom-events.js';
import { CommandPalette } from './components/command-palette.js';
import { openAdjustModal, openPriceModal } from './inventory-modals.js';
import { enterLabelMode, isLabelMode, exitLabelMode } from './label-selection.js';

// Explicit panel imports (no side effects until init() is called)
import { init as initInventoryModals } from './inventory-modals.js';
import { init as initInventoryPanel } from './inventory/inventory-panel.js';
import { init as initBomPanel } from './bom/bom-panel.js';
import { init as initImportPanel } from './import/import-panel.js';
import { init as initResizePanels } from './resize-panels.js';
import { init as initPartPreview } from './part-preview.js';
import { init as initGroupFlyout } from './group-flyout/flyout-panel.js';
import { init as initLabelSelection } from './label-selection.js';
import { init as initLabelExportModal } from './label-export-modal.js';
import { registerScanHandler } from './import/mfg-direct/mfg-direct-panel.js';
import { initKeyboardNav } from './a11y/keyboard-nav.js';
import { applyView, listViews } from './inventory/saved-views.js';
import invState from './inventory/inv-state.js';

// Expose globals for E2E tests and Python's evaluate_js
window.store = store;
window.EventBus = EventBus;
window.Events = Events;
window.processBOM = processBOM;
window.matchBOM = matchBOM;
window.colorizeRefs = colorizeRefs;
window.REF_COLOR_MAP = REF_COLOR_MAP;

// ── Init on pywebview ready ────────────────────────────
async function initApp() {
  setEnterSubmitEnabled(() => getShortcutPrefs().enterSubmitsModals);

  // Dismiss the startup splash once the grid has data (or after a safety timeout,
  // so a failed load never traps the user behind the overlay).
  const dismissSplash = () => {
    const el = document.getElementById("startup-splash");
    if (!el) return;
    el.classList.add("hide");
    setTimeout(() => el.remove(), 250);
  };
  EventBus.on(Events.INVENTORY_LOADED, dismissSplash);
  setTimeout(dismissSplash, 8000);

  // Initialize panels (explicit, no side-effect imports)
  initResizePanels();
  initInventoryModals();
  initInventoryPanel();
  initBomPanel();
  initImportPanel();
  initPartPreview();
  initGroupFlyout();
  initLabelSelection();
  initLabelExportModal();

  // ── Close confirmation modal ────────────────────────────
  const closeModal = Modal("close-modal", { cancelId: "close-cancel", confirmId: "close-save" });

  document.getElementById("close-discard").addEventListener("click", () => {
    closeModal.close();
    api("confirm_close");
  });

  document.getElementById("close-save").addEventListener("click", () => {
    closeModal.close();
    EventBus.emit(Events.SAVE_AND_CLOSE);
  });

  // Expose to Python's evaluate_js("closeModal.open()")
  window.closeModal = closeModal;

  // ── PnP consumption handler (called by pnp_server.py via evaluate_js) ──
  window._pnpConsume = function (freshInventory, detail) {
    onInventoryUpdated(freshInventory);
    AppLog.info("PnP: consumed " + detail.qty + "x " + detail.part_key + " (new_qty=" + detail.new_qty + ")");
    showToast("PnP: -" + detail.qty + " " + detail.part_key);
  };

  // ── Phone-scan PO handler (called by the scan server via evaluate_js) ──
  // Registers window._scanReceived to land OCR'd line items in the mfg-direct
  // staging editor (mirrors the _pnpConsume push pattern above).
  registerScanHandler();

  // ── Cross-panel designator hover highlighting ──────────
  var highlightedRef = null;
  document.addEventListener("mouseover", function (e) {
    var target = e.target.closest("[data-ref], [data-refs]");
    var ref = null;
    if (target) {
      ref = target.dataset.ref || null;
      // If hovering a range span, pick the first ref in the range for identity
      if (!ref && target.dataset.refs) ref = target.dataset.refs.split(" ")[0];
    }
    if (ref === highlightedRef) return;
    if (highlightedRef) {
      document.querySelectorAll(".ref-highlight").forEach(function (el) {
        el.classList.remove("ref-highlight");
      });
    }
    highlightedRef = ref;
    if (ref) {
      // Match individual refs (data-ref) and range spans (data-refs~= space-separated match)
      var escaped = CSS.escape(ref);
      document.querySelectorAll('[data-ref="' + escaped + '"], [data-refs~="' + escaped + '"]').forEach(function (el) {
        el.classList.add("ref-highlight");
        // Scroll highlighted ref into view within its scrollable .refs-scroll container
        var cell = el.closest(".refs-scroll") || el.closest(".refs-cell");
        if (cell && cell.scrollHeight > cell.clientHeight) {
          el.scrollIntoView({ block: "nearest", inline: "nearest" });
        }
      });
    }
  });

  // ── Prevent accidental file navigation ─────────────────
  document.addEventListener("dragover", e => e.preventDefault());
  document.addEventListener("drop", e => e.preventDefault());

  const clearBtn = document.getElementById("console-clear");
  if (clearBtn) clearBtn.addEventListener("click", () => AppLog.clear());

  // Preferences modal
  const prefsBtn = document.getElementById("prefs-btn");
  if (prefsBtn) prefsBtn.addEventListener("click", openPreferencesModal);
  const prefsSave = document.getElementById("prefs-save");
  if (prefsSave) prefsSave.addEventListener("click", applyPreferences);

  wireDigikeyButtons();

  // Vendors manager modal
  wireVendorsModal();

  const rebuildBtn = document.getElementById("rebuild-inv");
  if (rebuildBtn) rebuildBtn.addEventListener("click", async () => {
    AppLog.info("Rebuilding inventory...");
    const fresh = await api("rebuild_inventory");
    if (!fresh) return;
    onInventoryUpdated(fresh);
    showToast("Inventory rebuilt");
    AppLog.info("Inventory rebuilt: " + fresh.length + " parts");
  });

  // Register links undo handler (used by bom-panel + inventory-panel)
  UndoRedo.register("links", (action, data) => {
    if (action === "snapshot") {
      return {
        manualLinks: JSON.parse(JSON.stringify(store.links.manualLinks)),
        confirmedMatches: JSON.parse(JSON.stringify(store.links.confirmedMatches)),
      };
    }
    store.links.manualLinks = data.manualLinks;
    store.links.confirmedMatches = data.confirmedMatches;
    EventBus.emit(Events.LINKS_CHANGED);
    EventBus.emit(Events.CONFIRMED_CHANGED);
  });

  // Global undo/redo buttons + keyboard
  const globalUndo = document.getElementById("global-undo");
  const globalRedo = document.getElementById("global-redo");

  const isMac = navigator.platform.toUpperCase().includes("MAC");
  const mod = isMac ? "\u2318" : "Ctrl+";
  if (globalUndo) globalUndo.title = "Undo (" + mod + "Z)";
  if (globalRedo) globalRedo.title = "Redo (" + mod + "Shift+Z)";

  function syncUndoRedoButtons() {
    if (globalUndo) globalUndo.disabled = !UndoRedo.canUndo();
    if (globalRedo) globalRedo.disabled = !UndoRedo.canRedo();
  }

  if (globalUndo) globalUndo.addEventListener("click", async () => { await UndoRedo.undo(); syncUndoRedoButtons(); });
  if (globalRedo) globalRedo.addEventListener("click", async () => { await UndoRedo.redo(); syncUndoRedoButtons(); });

  EventBus.on(Events.INVENTORY_UPDATED, syncUndoRedoButtons);
  EventBus.on(Events.BOM_LOADED, syncUndoRedoButtons);

  const help = initShortcutHelp();

  // ── Command Palette ──────────────────────────────────────────────────────────

  /**
   * Derive the context for the palette from the currently focused inventory row.
   * @returns {{ focusedPartKey: string|null, bomMode: boolean }}
   */
  function buildPaletteContext() {
    const invBody = document.getElementById('inventory-body');
    let focusedPartKey = null;
    if (invBody) {
      // The roving-focus row has tabindex=0 and a data-part-id attribute
      const focused = invBody.querySelector('[data-part-id][tabindex="0"]');
      if (focused) focusedPartKey = focused.dataset.partId || null;
    }
    const bomMode = !!store.bomResults;
    return { focusedPartKey, bomMode };
  }

  /**
   * Build the full command set from the given context.
   * Reuses existing handlers — no duplicated logic.
   * @param {{ focusedPartKey: string|null, bomMode: boolean }} ctx
   */
  function getPaletteCommands(ctx) {
    // Reverse-lookup: mirror invPartKey()'s exact priority order so DigiKey/Pololu/
    // Mouser-only parts resolve correctly (not just LCSC and MPN).
    const focusedItem = ctx.focusedPartKey
      ? store.inventory.find(item => invPartKey(item) === ctx.focusedPartKey)
      : null;

    /** @type {Array<{id:string,label:string,hint?:string,group?:string,keywords?:string[],run:Function}>} */
    const cmds = [];

    // ── Global commands ──────────────────────────────────────────────────────
    cmds.push({
      id: 'open-preferences',
      label: 'Open Preferences',
      group: 'Global',
      keywords: ['settings', 'config', 'prefs'],
      run: () => openPreferencesModal(),
    });

    cmds.push({
      id: 'rebuild-inventory',
      label: 'Rebuild Inventory',
      group: 'Global',
      keywords: ['refresh', 'reload', 'sync'],
      run: async () => {
        AppLog.info('Rebuilding inventory…');
        const fresh = await api('rebuild_inventory');
        if (!fresh) return;
        onInventoryUpdated(fresh);
        showToast('Inventory rebuilt');
        AppLog.info('Inventory rebuilt: ' + fresh.length + ' parts');
      },
    });

    cmds.push({
      id: 'manage-vendors',
      label: 'Manage Vendors',
      group: 'Global',
      keywords: ['suppliers', 'vendors', 'distributor'],
      run: () => openVendorsModal(),
    });

    cmds.push({
      id: 'toggle-label-mode',
      label: isLabelMode() ? 'Exit Label Mode' : 'Print Labels',
      group: 'Global',
      keywords: ['labels', 'print', 'epson', 'tape'],
      run: () => {
        if (isLabelMode()) {
          exitLabelMode();
        } else {
          enterLabelMode();
        }
      },
    });

    cmds.push({
      id: 'cycle-grouping',
      label: 'Cycle Grouping',
      group: 'Global',
      keywords: ['group', 'hierarchy', 'flat', 'sections'],
      run: () => {
        // Delegate to the group-column header button: the real groupLevel cycle
        // logic lives inside inv-events.js setupEvents() as an inline delegated
        // handler on state.body — it is not exported as a standalone function.
        // Using .click() is intentional here to avoid duplicating that logic.
        const groupCell = document.querySelector('.inv-col-cell[data-col="group"]');
        if (groupCell) groupCell.click();
      },
    });

    cmds.push({
      id: 'show-shortcuts',
      label: 'Show Keyboard Shortcuts',
      group: 'Global',
      keywords: ['help', 'keyboard', 'hotkeys', 'bindings'],
      run: () => help.open(),
    });

    cmds.push({
      id: 'undo',
      label: 'Undo',
      group: 'Global',
      keywords: ['revert', 'history'],
      run: async () => {
        if (UndoRedo.canUndo()) { await UndoRedo.undo(); syncUndoRedoButtons(); }
      },
    });

    cmds.push({
      id: 'redo',
      label: 'Redo',
      group: 'Global',
      keywords: ['history', 'forward'],
      run: async () => {
        if (UndoRedo.canRedo()) { await UndoRedo.redo(); syncUndoRedoButtons(); }
      },
    });

    cmds.push({
      id: 'focus-import',
      label: 'Focus Import Panel',
      group: 'Global',
      keywords: ['panel', 'navigate', 'import'],
      run: () => {
        const el = document.getElementById('import-body');
        if (!el) return;
        if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
        el.focus();
      },
    });

    cmds.push({
      id: 'focus-inventory',
      label: 'Focus Inventory Panel',
      group: 'Global',
      keywords: ['panel', 'navigate', 'inventory'],
      run: () => {
        const el = document.getElementById('inventory-body');
        if (!el) return;
        if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
        el.focus();
      },
    });

    cmds.push({
      id: 'focus-bom',
      label: 'Focus BOM Panel',
      group: 'Global',
      keywords: ['panel', 'navigate', 'bom'],
      run: () => {
        const el = document.getElementById('bom-body');
        if (!el) return;
        if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
        el.focus();
      },
    });

    // ── BOM commands (when BOM is loaded) ────────────────────────────────────
    if (ctx.bomMode) {
      cmds.push({
        id: 'save-bom',
        label: 'Save BOM',
        group: 'BOM',
        keywords: ['export', 'file', 'csv'],
        run: () => saveBomFile(),
      });

      cmds.push({
        id: 'consume-bom',
        label: 'Consume BOM',
        group: 'BOM',
        keywords: ['deduct', 'subtract', 'use', 'consume'],
        run: () => {
          // The consume flow (modal open, arming, API call) is wired as an
          // inline handler in bom-events.js setupEvents() — not exported.
          // Using .click() is intentional; disabled guard prevents misfire.
          const btn = document.getElementById('bom-consume-btn');
          if (btn && !btn.disabled) btn.click();
        },
      });

      cmds.push({
        id: 'clear-bom',
        label: 'Clear BOM',
        group: 'BOM',
        keywords: ['remove', 'close', 'unload'],
        run: () => {
          // The clear flow (state reset, DOM wipe, event emission) is wired as
          // an inline handler in bom-events.js setupEvents() — not exported.
          // Using .click() is intentional; disabled guard prevents misfire.
          const btn = document.getElementById('bom-clear-btn');
          if (btn && !btn.disabled) btn.click();
        },
      });
    }

    // ── Saved Views commands ─────────────────────────────────────────────────
    cmds.push({
      id: 'save-current-view',
      label: 'Save current view…',
      group: 'Views',
      keywords: ['view', 'save', 'filter', 'search', 'snapshot'],
      run: () => {
        // Open the dropdown, then click the "Save current view…" item
        const btn = document.getElementById('saved-views-btn');
        if (btn) btn.click();
        setTimeout(() => {
          const saveItem = document.querySelector('.sv-save-item[data-action="save-view"]');
          if (saveItem) /** @type {HTMLElement} */ (saveItem).click();
        }, 50);
      },
    });

    const _savedViewsList = listViews();
    for (let _svi = 0; _svi < _savedViewsList.length; _svi++) {
      const _v = _savedViewsList[_svi];
      cmds.push({
        id: 'apply-view-' + _v.id,
        label: 'Apply view: ' + _v.name,
        group: 'Views',
        keywords: ['view', 'apply', _v.name.toLowerCase()],
        run: (function (viewSnapshot) {
          return function () {
            applyView(viewSnapshot, invState);
            window.dispatchEvent(new CustomEvent('inv-filter-changed'));
            if (invState._render) invState._render();
          };
        }(_v)),
      });
    }

    // ── Context commands (inventory row focused) ─────────────────────────────
    if (ctx.focusedPartKey) {
      const pk = ctx.focusedPartKey;

      cmds.push({
        id: 'adjust-part',
        label: 'Adjust ' + pk,
        group: 'Part',
        keywords: ['qty', 'quantity', 'adjust', 'edit'],
        run: () => {
          if (focusedItem) {
            openAdjustModal(focusedItem);
          } else {
            // Fall back to clicking the row's Adjust button
            const row = document.querySelector('[data-part-id="' + CSS.escape(pk) + '"]');
            const btn = row && row.querySelector('[data-action="adjust"], .adjust-btn');
            if (btn) btn.click();
          }
        },
      });

      cmds.push({
        id: 'edit-price',
        label: 'Edit price ' + pk,
        group: 'Part',
        keywords: ['price', 'cost', 'unit price'],
        run: () => {
          if (focusedItem) {
            openPriceModal(focusedItem);
          } else {
            const row = document.querySelector('[data-part-id="' + CSS.escape(pk) + '"]');
            const btn = row && row.querySelector('[data-action="price"], .price-btn');
            if (btn) btn.click();
          }
        },
      });

      if (ctx.bomMode) {
        cmds.push({
          id: 'link-part',
          label: 'Link ' + pk,
          group: 'Part',
          keywords: ['link', 'connect', 'match', 'bom'],
          run: () => {
            const row = document.querySelector('[data-part-id="' + CSS.escape(pk) + '"]');
            const btn = row && row.querySelector('[data-action="link"], .link-btn');
            if (btn) btn.click();
          },
        });
      }
    }

    return cmds;
  }

  const palette = CommandPalette({ getCommands: getPaletteCommands });

  initShortcuts({
    undo: async () => { if (UndoRedo.canUndo()) { await UndoRedo.undo(); syncUndoRedoButtons(); } },
    redo: async () => { if (UndoRedo.canRedo()) { await UndoRedo.redo(); syncUndoRedoButtons(); } },
    save: () => saveBomFile(),
    openPreferences: () => openPreferencesModal(),
    openPalette: () => { if (palette.isOpen()) palette.close(); else palette.open(buildPaletteContext()); },
    focusPanel: (n) => {
      const id = n === 1 ? 'import-body' : n === 2 ? 'inventory-body' : 'bom-body';
      const el = document.getElementById(id);
      if (!el) return;
      // panel-body elements are made focusable by makeScrollable (tabindex=0);
      // focus it directly so keyboard scrolling works immediately.
      if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
      el.focus();
    },
    exitMode: () => {
      const t = document.getElementById('label-toolbar');
      if (t && !t.classList.contains('hidden')) {
        const doneBtn = document.getElementById('label-done-btn');
        if (doneBtn) doneBtn.click();
        return;
      }
      if (store.links.linkingBomRow) store.links.setReverseLinkingMode(false);
      else if (store.links.linkingMode) store.links.setLinkingMode(false);
    },
    showHelp: () => help.open(),
  });

  initKeyboardNav();

  await whenPywebviewReady();
  // Startup-timing probe. The backend returns true only when DUBIS_BENCH_OUT is
  // set, so a single bridge round-trip gates all further marks — normal launches
  // pay one no-op call and emit nothing else. See bench.py / scripts/bench-startup.py.
  // Attach navigation timing so the harness can split document/module load from
  // the pywebview bridge handshake within the "window shown → bridge ready" gap.
  let navDetail = "";
  try {
    const nav = performance.getEntriesByType("navigation")[0];
    if (nav) navDetail = JSON.stringify({
      now: Math.round(performance.now()),
      responseEnd: Math.round(nav.responseEnd),
      domContentLoaded: Math.round(nav.domContentLoadedEventEnd),
      domComplete: Math.round(nav.domComplete),
      loadEnd: Math.round(nav.loadEventEnd),
    });
  } catch { /* navigation timing unavailable */ }
  const benchOn = await api("bench_mark", "js_pywebview_ready", navDetail).catch(() => false);
  if (benchOn) EventBus.on(Events.INVENTORY_LOADED, () => api("bench_mark", "js_inventory_loaded"));
  await loadPreferences();
  if (benchOn) api("bench_mark", "js_prefs_loaded");
  const { hydrateFromPreferences: hydrateInvView } = await import('./inventory/inv-state.js');
  hydrateInvView(store.preferences.inventory_view);
  loadInventory();
  api("check_digikey_session").then(function (r) {
    if (r && r.logged_in) {
      AppLog.info("Digikey: existing session found");
      // Cookie presence isn't enough — validate the session is actually live.
      // Hits a logged-in-only Digikey page in the hidden webview; on failure
      // the backend invalidates the session so the next preview tooltip
      // shows the "Login to Digikey in Preferences" message.
      api("validate_digikey_session").then(function (v) {
        if (!v) return;
        if (v.changed && !v.logged_in) {
          AppLog.warn("Digikey: " + (v.message || "session expired"));
          showToast("Digikey session expired — log in via Preferences");
        } else if (v.logged_in) {
          AppLog.info("Digikey: session validated");
        } else if (v.message) {
          AppLog.info("Digikey: " + v.message);
        }
      });
    } else if (r && r.message) AppLog.info("DK: " + r.message);
  });
}

if (document.readyState === "complete") {
  initApp();
} else {
  window.addEventListener("load", initApp);
}
