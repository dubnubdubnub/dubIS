/* main.js — Event bus, global state, inventory loading */

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

// ── Global State ───────────────────────────────────────
const App = {
  inventory: [],
  bomResults: null,
  bomFileName: "",
};

// ── Toast ──────────────────────────────────────────────
function showToast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2500);
}

// ── Escape HTML ────────────────────────────────────────
function escHtml(s) {
  const d = document.createElement("div");
  d.textContent = s || "";
  return d.innerHTML;
}

// ── API wrapper ────────────────────────────────────────
async function api(method, ...args) {
  return window.pywebview.api[method](...args);
}

// ── Load inventory and notify panels ───────────────────
async function loadInventory() {
  try {
    App.inventory = await api("get_inventory");
    document.getElementById("inv-count").textContent = App.inventory.length + " parts";
    EventBus.emit("inventory-loaded", App.inventory);
  } catch (e) {
    console.error("Failed to load inventory:", e);
    showToast("Error loading inventory");
  }
}

// After any mutation, refresh everything
function onInventoryUpdated(freshInventory) {
  App.inventory = freshInventory;
  document.getElementById("inv-count").textContent = App.inventory.length + " parts";
  EventBus.emit("inventory-updated", App.inventory);
}

// ── Prevent accidental file navigation ─────────────────
document.addEventListener("dragover", e => e.preventDefault());
document.addEventListener("drop", e => e.preventDefault());

// ── Init on pywebview ready ────────────────────────────
function initApp() {
  if (window.pywebview && window.pywebview.api) {
    loadInventory();
  } else {
    window.addEventListener("pywebviewready", loadInventory);
  }
}

if (document.readyState === "complete") {
  initApp();
} else {
  window.addEventListener("load", initApp);
}
