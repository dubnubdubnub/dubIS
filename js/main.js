/* main.js — Event bus, global state, inventory loading */

// ── Constants ──────────────────────────────────────────
const TOAST_DURATION_MS = 2500;
const UNDO_MAX_HISTORY = 500;
const LOG_MAX_ENTRIES = 200;
const PREFS_MAX_THRESHOLD = 200;
const PREFS_MIN_THRESHOLD = 5;

const STOCK_COLOR_STOPS = [
  { r: 248, g: 81, b: 73 },   // #f85149  red
  { r: 240, g: 136, b: 62 },  // #f0883e  orange
  { r: 210, g: 153, b: 34 },  // #d29922  yellow
  { r: 63, g: 185, b: 80 },   // #3fb950  green
];

// ── Event Names ────────────────────────────────────────
const Events = {
  INVENTORY_LOADED:  "inventory-loaded",
  INVENTORY_UPDATED: "inventory-updated",
  BOM_LOADED:        "bom-loaded",
  BOM_CLEARED:       "bom-cleared",
  PREFS_CHANGED:     "preferences-changed",
  CONFIRMED_CHANGED: "confirmed-match-changed",
  LINKING_MODE:      "linking-mode",
  SAVE_AND_CLOSE:    "save-and-close",
};

// ── Event Bus ──────────────────────────────────────────
const EventBus = {
  _listeners: {},
  on(event, fn) {
    (this._listeners[event] = this._listeners[event] || []).push(fn);
  },
  off(event, fn) {
    const list = this._listeners[event];
    if (list) this._listeners[event] = list.filter(f => f !== fn);
  },
  emit(event, data) {
    (this._listeners[event] || []).forEach(fn => fn(data));
  }
};

// ── Global Undo/Redo ──────────────────────────────────
const UndoRedo = {
  _undo: [],
  _redo: [],
  _max: UNDO_MAX_HISTORY,
  _restorers: {},  // panel → fn(action, data)

  register(panel, restoreFn) { this._restorers[panel] = restoreFn; },

  save(panel, data) {
    this._undo.push({ panel, data: JSON.parse(JSON.stringify(data)) });
    if (this._undo.length > this._max) this._undo.shift();
    this._redo = [];
  },

  undo() {
    if (!this._undo.length) return false;
    const entry = this._undo.pop();
    const current = this._restorers[entry.panel]("snapshot");
    this._redo.push({ panel: entry.panel, data: current });
    this._restorers[entry.panel]("restore", entry.data);
    AppLog.info("Undo (" + entry.panel + ")");
    return true;
  },

  redo() {
    if (!this._redo.length) return false;
    const entry = this._redo.pop();
    const current = this._restorers[entry.panel]("snapshot");
    this._undo.push({ panel: entry.panel, data: current });
    this._restorers[entry.panel]("restore", entry.data);
    AppLog.info("Redo (" + entry.panel + ")");
    return true;
  },

  canUndo() { return this._undo.length > 0; },
  canRedo() { return this._redo.length > 0; },
};

// ── Global State ───────────────────────────────────────
const App = {
  // ── Data (owned by main.js, set via API) ──
  inventory: [],

  // ── BOM state (owned by bom-panel.js) ──
  bomResults: null,
  bomFileName: "",
  bomHeaders: [],
  bomCols: {},
  bomDirty: false,

  // ── Linking state (central mutation API) ──
  links: {
    manualLinks: [],
    confirmedMatches: [],
    linkingMode: false,
    linkingInvItem: null,

    addManualLink(bk, ipk) {
      this.manualLinks.push({ bomKey: bk, invPartKey: ipk });
    },
    confirmMatch(bk, ipk) {
      this.confirmedMatches = this.confirmedMatches.filter(c => c.bomKey !== bk);
      this.confirmedMatches.push({ bomKey: bk, invPartKey: ipk });
      EventBus.emit(Events.CONFIRMED_CHANGED);
    },
    unconfirmMatch(bk) {
      this.confirmedMatches = this.confirmedMatches.filter(c => c.bomKey !== bk);
      EventBus.emit(Events.CONFIRMED_CHANGED);
    },
    setLinkingMode(active, invItem) {
      this.linkingMode = active;
      this.linkingInvItem = active ? invItem : null;
      EventBus.emit(Events.LINKING_MODE, { active, invItem: this.linkingInvItem });
    },
    loadFromSaved(savedLinks) {
      if (Array.isArray(savedLinks)) {
        this.manualLinks = savedLinks;
        this.confirmedMatches = [];
      } else if (savedLinks && typeof savedLinks === "object") {
        this.manualLinks = Array.isArray(savedLinks.manualLinks) ? savedLinks.manualLinks : [];
        this.confirmedMatches = Array.isArray(savedLinks.confirmedMatches) ? savedLinks.confirmedMatches : [];
      } else {
        this.manualLinks = [];
        this.confirmedMatches = [];
      }
      this.linkingMode = false;
      this.linkingInvItem = null;
    },
    clearAll() {
      this.manualLinks = [];
      this.confirmedMatches = [];
      this.linkingMode = false;
      this.linkingInvItem = null;
    },
    hasLinks() {
      return this.manualLinks.length > 0 || this.confirmedMatches.length > 0;
    },
  },

  // ── Configuration (read-only) ──
  SECTION_ORDER: [
    "Connectors", "Switches", "Passives - Resistors", "Passives - Capacitors",
    "Passives - Inductors", "LEDs", "Crystals & Oscillators", "Diodes",
    "Discrete Semiconductors", "ICs - Microcontrollers",
    "ICs - Power / Voltage Regulators", "ICs - Voltage References",
    "ICs - Sensors", "ICs - Amplifiers", "ICs - Motor Drivers",
    "ICs - Interface", "ICs - ESD Protection", "Mechanical & Hardware", "Other",
  ],

  // ── Preferences (owned by main.js) ──
  preferences: { thresholds: {} },
};

// ── Toast ──────────────────────────────────────────────
function showToast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), TOAST_DURATION_MS);
}

// ── Escape HTML ────────────────────────────────────────
function escHtml(s) {
  const d = document.createElement("div");
  d.textContent = s || "";
  return d.innerHTML;
}

// ── Console Log ───────────────────────────────────────
const AppLog = {
  _entries: [],
  _max: LOG_MAX_ENTRIES,
  _add(level, msg) {
    const entry = { level, msg, time: new Date() };
    this._entries.push(entry);
    if (this._entries.length > this._max) this._entries.shift();
    const el = document.getElementById("console-entries");
    if (!el) return;
    const div = document.createElement("div");
    div.className = "console-entry console-" + level;
    const t = entry.time.toLocaleTimeString([], {hour:"2-digit",minute:"2-digit",second:"2-digit"});
    div.innerHTML = `<span class="console-time">${t}</span>${escHtml(msg)}`;
    el.appendChild(div);
    while (el.children.length > this._max) el.removeChild(el.firstChild);
    el.scrollTop = el.scrollHeight;
  },
  info(msg)  { this._add("info", msg); },
  warn(msg)  { this._add("warn", msg); },
  error(msg) { this._add("error", msg); },
  clear() {
    this._entries = [];
    const el = document.getElementById("console-entries");
    if (el) el.innerHTML = "";
  }
};

// ── API wrapper ────────────────────────────────────────
async function api(method, ...args) {
  try {
    return await window.pywebview.api[method](...args);
  } catch (e) {
    AppLog.error(method + ": " + e.message);
    showToast("Error: " + e.message);
    return undefined;
  }
}

// ── Modal lifecycle helper ──────────────────────────────
function Modal(id, { onClose, cancelId } = {}) {
  const el = document.getElementById(id);
  function open()  { el.classList.remove("hidden"); }
  function close() { el.classList.add("hidden"); if (onClose) onClose(); }
  el.addEventListener("click", (e) => { if (e.target === el) close(); });
  if (cancelId) document.getElementById(cancelId).addEventListener("click", close);
  document.addEventListener("keydown", (e) => {
    if (el.classList.contains("hidden")) return;
    if (e.key === "Escape") close();
  });
  return { el, open, close };
}

// ── Drop-zone wiring ────────────────────────────────────
function setupDropZone(zoneId, inputId, onBrowse, onFile) {
  const zone = document.getElementById(zoneId);
  const input = document.getElementById(inputId);
  zone.addEventListener("click", (e) => { if (e.target.tagName !== "INPUT") onBrowse(); });
  zone.addEventListener("dragover", (e) => { e.preventDefault(); e.stopPropagation(); zone.classList.add("dragover"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault(); e.stopPropagation(); zone.classList.remove("dragover");
    if (e.dataTransfer.files.length) onFile(e.dataTransfer.files[0]);
  });
  input.addEventListener("change", () => { if (input.files.length) onFile(input.files[0]); });
}

function resetDropZoneInput(inputId, onFile) {
  const input = document.getElementById(inputId);
  if (input) input.addEventListener("change", () => { if (input.files.length) onFile(input.files[0]); });
}

// ── Unit ↔ Ext price auto-calc ──────────────────────────
function linkPriceInputs(unitEl, extEl, getQty) {
  unitEl.addEventListener("input", () => {
    const up = parseFloat(unitEl.value), qty = getQty();
    if (!isNaN(up) && qty > 0) extEl.value = (up * qty).toFixed(2);
  });
  extEl.addEventListener("input", () => {
    const ep = parseFloat(extEl.value), qty = getQty();
    if (!isNaN(ep) && qty > 0) unitEl.value = (ep / qty).toFixed(4);
  });
}

// ── Preferences (persisted to data/preferences.json) ────
async function loadPreferences() {
  const stored = await api("load_preferences");
  if (stored && typeof stored === "object") {
    if (stored.thresholds) App.preferences.thresholds = stored.thresholds;
    if (stored.lastBomDir) App.preferences.lastBomDir = stored.lastBomDir;
    if (stored.lastImportDir) App.preferences.lastImportDir = stored.lastImportDir;
    if (stored.lastBomFile) App.preferences.lastBomFile = stored.lastBomFile;
  }
}

async function savePreferences() {
  await api("save_preferences", JSON.stringify(App.preferences));
}

function getThreshold(section) {
  return App.preferences.thresholds[section] ?? 50;
}

function setThreshold(section, value) {
  App.preferences.thresholds[section] = value;
  savePreferences();
  EventBus.emit(Events.PREFS_CHANGED);
}

// ── Stock Value → Color (4-stop RGB lerp) ──────────────
function stockValueColor(stockValue, threshold) {
  if (threshold <= 0) return "#3fb950";
  const ratio = Math.min(Math.max(stockValue / threshold, 0), 1);

  // 4 stops: 0=red, 1/3=orange, 2/3=yellow, 1=green
  const stops = STOCK_COLOR_STOPS;

  const t = ratio * 3; // scale to [0, 3]
  const i = Math.min(Math.floor(t), 2);
  const f = t - i;
  const a = stops[i], b = stops[i + 1];
  const r = Math.round(a.r + (b.r - a.r) * f);
  const g = Math.round(a.g + (b.g - a.g) * f);
  const bl = Math.round(a.b + (b.b - a.b) * f);
  return `rgb(${r},${g},${bl})`;
}

// ── Preferences Modal ──────────────────────────────────
const prefsModal = Modal("prefs-modal", { cancelId: "prefs-cancel" });

function updateSliderTrack(slider) {
  const pct = ((slider.value - slider.min) / (slider.max - slider.min)) * 100;
  // Map the 4-stop gradient into the filled portion, dark track for the rest
  const g = `linear-gradient(to right, `
    + `#f85149 0%, #f0883e ${pct * 0.33}%, #d29922 ${pct * 0.66}%, #3fb950 ${pct}%, `
    + `#30363d ${pct}%, #30363d 100%)`;
  slider.style.background = g;
}

function openPreferencesModal() {
  const container = document.getElementById("prefs-sliders");
  container.innerHTML = "";

  App.SECTION_ORDER.forEach(section => {
    const val = getThreshold(section);
    const row = document.createElement("div");
    row.className = "prefs-row";
    row.innerHTML = `
      <label class="prefs-label">${escHtml(section)}</label>
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

    container.appendChild(row);
  });

  prefsModal.open();
}

function closePreferencesModal() {
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

// ── Update header stats ────────────────────────────────
function updateInventoryHeader() {
  document.getElementById("inv-count").textContent = App.inventory.length + " parts";
  const total = App.inventory.reduce((sum, item) => sum + item.qty * (item.unit_price || 0), 0);
  document.getElementById("inv-total-value").textContent = "$" + total.toFixed(2);
}

// ── Load inventory and notify panels ───────────────────
async function loadInventory() {
  const fresh = await api("rebuild_inventory");
  if (!fresh) return;
  App.inventory = fresh;
  updateInventoryHeader();
  EventBus.emit(Events.INVENTORY_LOADED, App.inventory);
  AppLog.info("Loaded inventory: " + App.inventory.length + " parts");
}

// After any mutation, refresh everything
function onInventoryUpdated(freshInventory) {
  App.inventory = freshInventory;
  updateInventoryHeader();
  EventBus.emit(Events.INVENTORY_UPDATED, App.inventory);
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

function handleWindowClose() {
  if (!App.bomDirty) {
    api("confirm_close");
    return;
  }
  closeModal.open();
}

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

  // Preferences modal (cancel + backdrop + Escape handled by Modal)
  const prefsBtn = document.getElementById("prefs-btn");
  if (prefsBtn) prefsBtn.addEventListener("click", openPreferencesModal);
  const prefsSave = document.getElementById("prefs-save");
  if (prefsSave) prefsSave.addEventListener("click", applyPreferences);

  const rebuildBtn = document.getElementById("rebuild-inv");
  if (rebuildBtn) rebuildBtn.addEventListener("click", async () => {
    AppLog.info("Rebuilding inventory...");
    const fresh = await api("rebuild_inventory");
    if (!fresh) return;
    onInventoryUpdated(fresh);
    showToast("Inventory rebuilt");
    AppLog.info("Inventory rebuilt: " + fresh.length + " parts");
  });

  // Global undo/redo buttons + keyboard
  const globalUndo = document.getElementById("global-undo");
  const globalRedo = document.getElementById("global-redo");

  function syncUndoRedoButtons() {
    if (globalUndo) globalUndo.disabled = !UndoRedo.canUndo();
    if (globalRedo) globalRedo.disabled = !UndoRedo.canRedo();
  }

  if (globalUndo) globalUndo.addEventListener("click", () => { UndoRedo.undo(); syncUndoRedoButtons(); });
  if (globalRedo) globalRedo.addEventListener("click", () => { UndoRedo.redo(); syncUndoRedoButtons(); });

  // Keep buttons in sync after any undo/redo action or data change
  EventBus.on(Events.INVENTORY_UPDATED, syncUndoRedoButtons);
  EventBus.on(Events.BOM_LOADED, syncUndoRedoButtons);

  document.addEventListener("keydown", (e) => {
    if (!e.ctrlKey || e.altKey) return;
    if (e.key === "z" && !e.shiftKey && UndoRedo.canUndo()) {
      e.preventDefault();
      UndoRedo.undo();
      syncUndoRedoButtons();
    } else if (e.key === "Z" && e.shiftKey && UndoRedo.canRedo()) {
      e.preventDefault();
      UndoRedo.redo();
      syncUndoRedoButtons();
    }
  });

  if (window.pywebview && window.pywebview.api) {
    loadInventory();
  } else {
    window.addEventListener("pywebviewready", async () => {
      await loadPreferences();
      loadInventory();
    });
  }
}

if (document.readyState === "complete") {
  initApp();
} else {
  window.addEventListener("load", initApp);
}
