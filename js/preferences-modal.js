/* preferences-modal.js — Preferences modal with section threshold sliders
   and Digikey login/logout flow. Extracted from app-init.js. */

import { api, AppLog } from './api.js';
import { showToast, escHtml, Modal } from './ui-helpers.js';
import { EventBus, Events } from './event-bus.js';
import { store, getThreshold, savePreferences } from './store.js';

var PREFS_MAX_THRESHOLD = 200;
var PREFS_MIN_THRESHOLD = 5;

// ── Digikey login polling ──
var _dkPollTimer = null;
function stopDkPolling() {
  if (_dkPollTimer) { clearTimeout(_dkPollTimer); _dkPollTimer = null; }
}

// ── Modal instance ──
const prefsModal = Modal("prefs-modal", { cancelId: "prefs-cancel" });

// ── Slider helpers ──

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
  const labelStyle = indent
    ? 'font-size:11px;color:var(--text-secondary);text-indent:18px'
    : '';
  row.innerHTML = `
    <label class="prefs-label"${labelStyle ? ` style="${labelStyle}"` : ''}>${escHtml(indent ? section.split(" > ").pop() : section)}</label>
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

// ── Open / Close / Apply ──

export function openPreferencesModal() {
  const container = document.getElementById("prefs-sliders");
  container.innerHTML = "";

  store.SECTION_HIERARCHY.forEach(entry => {
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
      if (result && result.message) AppLog.info("DK: " + result.message);
    }
  });

  prefsModal.open();
}

function closePreferencesModal() {
  stopDkPolling();
  prefsModal.close();
}

export function applyPreferences() {
  const rows = document.querySelectorAll("#prefs-sliders .prefs-row");
  rows.forEach(row => {
    const section = row.querySelector(".prefs-slider").dataset.section;
    const val = parseInt(row.querySelector(".prefs-input").value, 10);
    store.preferences.thresholds[section] = isNaN(val) || val < 0 ? 0 : val;
  });

  savePreferences();
  closePreferencesModal();
  EventBus.emit(Events.PREFS_CHANGED);
}

// ── Digikey login/logout button wiring ──

export function wireDigikeyButtons() {
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
          dkStatus.style.color = "var(--color-red)";
          dkLoginBtn.classList.remove("hidden");
        } else if (result && result.status === "error") {
          stopDkPolling();
          dkStatus.textContent = result.message;
          dkStatus.style.color = "var(--color-red)";
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
}
