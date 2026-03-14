/* app-init.js — Application entry point: wires up modals, global shortcuts, loads inventory */

import { EventBus, Events } from './event-bus.js';
import { api, AppLog } from './api.js';
import { showToast, Modal } from './ui-helpers.js';
import { UndoRedo } from './undo-redo.js';
import { App, loadPreferences, loadInventory, loadKicadState, onInventoryUpdated } from './store.js';
import { processBOM } from './csv-parser.js';
import { matchBOM } from './matching.js';
import { colorizeRefs, REF_COLOR_MAP } from './part-keys.js';
import { openPreferencesModal, applyPreferences, wireDigikeyButtons } from './preferences-modal.js';

// Side-effect imports: each panel self-initialises on import
import './inventory-modals.js';
import './inventory-panel.js';
import './bom-panel.js';
import './import-panel.js';
import './resize-panels.js';
import './part-preview.js';
import './kicad-panel.js';
import './openpnp-modal.js';

// Expose globals for E2E tests and Python's evaluate_js
window.App = App;
window.EventBus = EventBus;
window.Events = Events;
window.processBOM = processBOM;
window.matchBOM = matchBOM;
window.colorizeRefs = colorizeRefs;
window.REF_COLOR_MAP = REF_COLOR_MAP;

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
  var target = e.target.closest("[data-ref]");
  var ref = target ? target.dataset.ref : null;
  if (ref === highlightedRef) return;
  if (highlightedRef) {
    document.querySelectorAll(".ref-highlight").forEach(function (el) {
      el.classList.remove("ref-highlight");
    });
  }
  highlightedRef = ref;
  if (ref) {
    document.querySelectorAll('[data-ref="' + CSS.escape(ref) + '"]').forEach(function (el) {
      el.classList.add("ref-highlight");
    });
  }
});

// ── Prevent accidental file navigation ─────────────────
document.addEventListener("dragover", e => e.preventDefault());
document.addEventListener("drop", e => e.preventDefault());

// ── Init on pywebview ready ────────────────────────────
async function initApp() {
  if (window.pywebview && window.pywebview.api) {
    await loadPreferences();
  }

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
        manualLinks: JSON.parse(JSON.stringify(App.links.manualLinks)),
        confirmedMatches: JSON.parse(JSON.stringify(App.links.confirmedMatches)),
      };
    }
    App.links.manualLinks = data.manualLinks;
    App.links.confirmedMatches = data.confirmedMatches;
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
    // Let browser handle undo/redo inside grid edit input
    if (window._activeGrid && window._activeGrid.isEditing()) return;
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
    loadKicadState();
    api("check_digikey_session").then(function (r) {
      if (r && r.logged_in) AppLog.info("Digikey: existing session found");
      else if (r && r.message) AppLog.info("DK: " + r.message);
    });
  } else {
    window.addEventListener("pywebviewready", async () => {
      await loadPreferences();
      loadInventory();
      loadKicadState();
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
