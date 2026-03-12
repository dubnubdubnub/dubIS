/* app-init.js — Application entry point: wires up modals, global shortcuts, loads inventory */

import { EventBus, Events } from './event-bus.js';
import { api, AppLog } from './api.js';
import { showToast, escHtml, Modal } from './ui-helpers.js';
import { UndoRedo } from './undo-redo.js';
import { App, loadPreferences, savePreferences, getThreshold, loadInventory, onInventoryUpdated } from './store.js';
import { processBOM } from './csv-parser.js';
import { matchBOM } from './matching.js';
import { colorizeRefs, REF_COLOR_MAP } from './part-keys.js';

// Side-effect imports: each panel self-initialises on import
import './inventory-panel.js';
import './bom-panel.js';
import './import-panel.js';
import './resize-panels.js';
import './part-preview.js';

// Expose globals for E2E tests and Python's evaluate_js
window.App = App;
window.EventBus = EventBus;
window.Events = Events;
window.processBOM = processBOM;
window.matchBOM = matchBOM;
window.colorizeRefs = colorizeRefs;
window.REF_COLOR_MAP = REF_COLOR_MAP;

var PREFS_MAX_THRESHOLD = 200;
var PREFS_MIN_THRESHOLD = 5;

// ── Preferences Modal ──────────────────────────────────
var _dkPollTimer = null;
function stopDkPolling() {
  if (_dkPollTimer) { clearTimeout(_dkPollTimer); _dkPollTimer = null; }
}
const prefsModal = Modal("prefs-modal", { cancelId: "prefs-cancel" });

function updateSliderTrack(slider) {
  const pct = ((slider.value - slider.min) / (slider.max - slider.min)) * 100;
  const g = `linear-gradient(to right, `
    + `#f85149 0%, #f0883e ${pct * 0.33}%, #d29922 ${pct * 0.66}%, #3fb950 ${pct}%, `
    + `#30363d ${pct}%, #30363d 100%)`;
  slider.style.background = g;
}

function _createPrefsSliderRow(section, indent) {
  const val = getThreshold(section);
  const row = document.createElement("div");
  row.className = "prefs-row";
  if (indent) row.style.paddingLeft = "18px";
  row.innerHTML = `
    <label class="prefs-label" ${indent ? 'style="font-size:11px;color:var(--text-secondary)"' : ''}>${escHtml(indent ? section.split(" > ").pop() : section)}</label>
    <input type="range" class="prefs-slider" min="0" max="${Math.max(val, PREFS_MAX_THRESHOLD)}" step="1" value="${val}" data-section="${escHtml(section)}">
    <span class="prefs-value-wrap">$<input type="number" class="prefs-input" min="0" step="1" value="${val}"></span>
  `;
  const slider = row.querySelector(".prefs-slider");
  const input = row.querySelector(".prefs-input");
  updateSliderTrack(slider);

  slider.addEventListener("input", () => {
    input.value = slider.value;
    updateSliderTrack(slider);
  });

  input.addEventListener("input", () => {
    const v = parseInt(input.value, 10);
    if (isNaN(v) || v < 0) return;
    slider.max = Math.max(v, PREFS_MIN_THRESHOLD);
    slider.value = v;
    updateSliderTrack(slider);
  });

  input.addEventListener("blur", () => {
    let v = parseInt(input.value, 10);
    if (isNaN(v) || v < 0) v = 0;
    input.value = v;
    slider.max = Math.max(v, PREFS_MIN_THRESHOLD);
    slider.value = v;
    updateSliderTrack(slider);
  });

  return row;
}

function openPreferencesModal() {
  const container = document.getElementById("prefs-sliders");
  container.innerHTML = "";

  App.SECTION_HIERARCHY.forEach(entry => {
    // Parent slider (always shown)
    container.appendChild(_createPrefsSliderRow(entry.name, false));
    // Subcategory sliders (indented)
    if (entry.children) {
      entry.children.forEach(child => {
        container.appendChild(_createPrefsSliderRow(entry.name + " > " + child, true));
      });
    }
  });

  // Load Digikey login status
  var dkStatus = document.getElementById("dk-status");
  var dkLoginBtn = document.getElementById("dk-login");
  var dkLogoutBtn = document.getElementById("dk-logout");
  dkStatus.textContent = "Checking...";
  dkStatus.style.color = "var(--text-muted)";
  api("get_digikey_login_status").then(function (result) {
    if (result && result.logged_in) {
      dkStatus.textContent = "Logged in";
      dkStatus.style.color = "var(--color-green)";
      dkLoginBtn.classList.add("hidden");
      dkLogoutBtn.classList.remove("hidden");
    } else {
      dkStatus.textContent = "Not logged in";
      dkStatus.style.color = "var(--text-muted)";
      dkLoginBtn.classList.remove("hidden");
      dkLogoutBtn.classList.add("hidden");
    }
  });

  prefsModal.open();
}

function closePreferencesModal() {
  stopDkPolling();
  prefsModal.close();
}

function applyPreferences() {
  const rows = document.querySelectorAll("#prefs-sliders .prefs-row");
  rows.forEach(row => {
    const section = row.querySelector(".prefs-slider").dataset.section;
    const val = parseInt(row.querySelector(".prefs-input").value, 10);
    App.preferences.thresholds[section] = isNaN(val) || val < 0 ? 0 : val;
  });

  savePreferences();
  closePreferencesModal();
  EventBus.emit(Events.PREFS_CHANGED);
}

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

  var dkLoginBtn = document.getElementById("dk-login");
  var dkLogoutBtn = document.getElementById("dk-logout");

  if (dkLoginBtn) dkLoginBtn.addEventListener("click", async () => {
    await api("start_digikey_login");
    var dkStatus = document.getElementById("dk-status");
    dkStatus.textContent = "Browser opened — waiting for login...";
    dkStatus.style.color = "var(--text-muted)";
    dkLoginBtn.classList.add("hidden");

    stopDkPolling();
    function pollDkLogin() {
      _dkPollTimer = setTimeout(async () => {
        var result = await api("sync_digikey_cookies");
        if (result && result.debug) {
          result.debug.forEach(function (line) { AppLog.info("  DK: " + line); });
        }
        if (result && result.logged_in) {
          stopDkPolling();
          var label = "Logged in" + (result.browser ? " (via " + result.browser + ")" : "");
          dkStatus.textContent = label;
          dkStatus.style.color = "var(--color-green)";
          dkLogoutBtn.classList.remove("hidden");
          showToast(label);
          AppLog.info("DK login success: " + label);
        } else if (result && result.status === "browser_running") {
          stopDkPolling();
          dkStatus.textContent = result.message;
          dkStatus.style.color = "var(--color-red, #e74c3c)";
          dkLoginBtn.classList.remove("hidden");
        } else if (result && result.status === "error") {
          stopDkPolling();
          dkStatus.textContent = result.message;
          dkStatus.style.color = "var(--color-red, #e74c3c)";
          dkLoginBtn.classList.remove("hidden");
          AppLog.warn("DK: " + result.message);
        } else {
          dkStatus.textContent = (result && result.message) || "Waiting for login...";
          pollDkLogin();
        }
      }, 1500);
    }
    pollDkLogin();
  });

  if (dkLogoutBtn) dkLogoutBtn.addEventListener("click", async () => {
    stopDkPolling();
    await api("logout_digikey");
    var dkStatus = document.getElementById("dk-status");
    dkStatus.textContent = "Not logged in";
    dkStatus.style.color = "var(--text-muted)";
    dkLoginBtn.classList.remove("hidden");
    dkLogoutBtn.classList.add("hidden");
    showToast("Digikey logged out");
  });

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
    });
  } else {
    window.addEventListener("pywebviewready", async () => {
      await loadPreferences();
      loadInventory();
      api("check_digikey_session").then(function (r) {
        if (r && r.logged_in) AppLog.info("Digikey: existing session found");
      });
    });
  }
}

if (document.readyState === "complete") {
  initApp();
} else {
  window.addEventListener("load", initApp);
}
