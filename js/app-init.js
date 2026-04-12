/* app-init.js — Application entry point: wires up modals, global shortcuts, loads inventory */

import { EventBus, Events } from './event-bus.js';
import { api, AppLog } from './api.js';
import { showToast, Modal } from './ui-helpers.js';
import { UndoRedo } from './undo-redo.js';
import { store, loadPreferences, loadInventory, onInventoryUpdated } from './store.js';
import { processBOM } from './csv-parser.js';
import { matchBOM } from './matching.js';
import { colorizeRefs, REF_COLOR_MAP } from './part-keys.js';
import { openPreferencesModal, applyPreferences, wireDigikeyButtons } from './preferences-modal.js';

// Explicit panel imports (no side effects until init() is called)
import { init as initInventoryModals } from './inventory-modals.js';
import { init as initInventoryPanel } from './inventory/inventory-panel.js';
import { init as initBomPanel } from './bom/bom-panel.js';
import { init as initImportPanel } from './import/import-panel.js';
import { init as initResizePanels } from './resize-panels.js';
import { init as initPartPreview } from './part-preview.js';
import { init as initGroupFlyout } from './group-flyout/flyout-panel.js';

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
  if (window.pywebview && window.pywebview.api) {
    await loadPreferences();
  }

  // Initialize panels (explicit, no side-effect imports)
  initResizePanels();
  initInventoryModals();
  initInventoryPanel();
  initBomPanel();
  initImportPanel();
  initPartPreview();
  initGroupFlyout();

  // ── Close confirmation modal ────────────────────────────
  const closeModal = Modal("close-modal", { cancelId: "close-cancel" });

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

  document.addEventListener("keydown", async (e) => {
    if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
    if (e.key === "z" && !e.shiftKey && UndoRedo.canUndo()) {
      e.preventDefault();
      await UndoRedo.undo();
      syncUndoRedoButtons();
    } else if (e.key === "Z" && e.shiftKey && UndoRedo.canRedo()) {
      e.preventDefault();
      await UndoRedo.redo();
      syncUndoRedoButtons();
    }
  });

  if (window.pywebview && window.pywebview.api) {
    loadInventory();
    api("check_digikey_session").then(function (r) {
      if (r && r.logged_in) AppLog.info("Digikey: existing session found");
      else if (r && r.message) AppLog.info("DK: " + r.message);
    });
  } else {
    window.addEventListener("pywebviewready", async () => {
      await loadPreferences();
      loadInventory();
      api("check_digikey_session").then(function (r) {
        if (r && r.logged_in) AppLog.info("Digikey: existing session found");
        else if (r && r.message) AppLog.info("DK: " + r.message);
      });
    });
  }
}

if (document.readyState === "complete") {
  initApp();
} else {
  window.addEventListener("load", initApp);
}
