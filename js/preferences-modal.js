/* preferences-modal.js — Preferences modal with section threshold sliders,
   Mouser API key management, and the wiring for three live sections.

   Three sections live in their own modules because they are live components
   with a start/stop lifecycle, not static form controls: the server picker
   (js/server-list.js), the JLCPCB pairing panel (js/jlc-sessions.js) and the
   DigiKey sign-in panel (js/digikey-pairing.js). All three are started when
   this modal opens and stopped when it closes. */

import { api, AppLog } from './api.js';
import { showToast, escHtml, Modal } from './ui-helpers.js';
import { store, getThreshold, savePreferences, preferencesSignal, getShortcutPrefs, setShortcutPrefs, getBehaviorPrefs, setBehaviorPrefs } from './store.js';
import { wireServerList, startServerList, stopServerList } from './server-list.js';
import { wireJlcPanel, startJlcPanel, stopJlcPanel } from './jlc-sessions.js';
import { wireDigikeyPanel, startDigikeyPanel, stopDigikeyPanel } from './digikey-pairing.js';

var PREFS_MAX_THRESHOLD = 200;
var PREFS_MIN_THRESHOLD = 5;

// ── Digikey sign-in polling ──
// Stops the pairing-code countdown and the login-status poll and drops any
// live code (js/digikey-pairing.js). Must run on every way the modal closes.
function stopDkPolling() {
  stopDigikeyPanel();
}

// ── Modal instance ──
const prefsModal = Modal("prefs-modal", {
  cancelId: "prefs-cancel",
  confirmId: "prefs-save",
  // Three sections of this modal are live while it is on screen and must not
  // be while it is not: the server picker polls /v1/health, and the JLC and
  // DigiKey panels count a pairing code down (and poll for the extension's
  // delivery). Cancel, Escape and a backdrop click all land here, not in
  // closePreferencesModal, so the DigiKey poll has to stop here too.
  onClose: function () { stopServerList(); stopJlcPanel(); stopDkPolling(); },
});

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

// ── Keyboard prefs ──

function syncKeyboardPrefs() {
  const p = getShortcutPrefs();
  document.getElementById('pref-redo').value = p.redo;
  document.getElementById('pref-enter-submit').checked = p.enterSubmitsModals;
  document.getElementById('pref-vim-nav').checked = p.vimNav;
  document.getElementById('pref-auto-copy').checked = getBehaviorPrefs().autoCopySelection;
  document.getElementById('pref-reel-ceiling').value = String(getBehaviorPrefs().reelCeiling);
}

function wireKeyboardPrefs() {
  document.getElementById('pref-redo').addEventListener('change', (e) => setShortcutPrefs({ redo: e.target.value }));
  document.getElementById('pref-enter-submit').addEventListener('change', (e) => setShortcutPrefs({ enterSubmitsModals: e.target.checked }));
  document.getElementById('pref-vim-nav').addEventListener('change', (e) => setShortcutPrefs({ vimNav: e.target.checked }));
  document.getElementById('pref-auto-copy').addEventListener('change', (e) => setBehaviorPrefs({ autoCopySelection: e.target.checked }));
  // Committed on 'change' (blur or Enter), not 'input': every keystroke of
  // "150" passes through 1 and 15, and persisting those would rewrite the rule
  // twice on the way to the number the user meant. setBehaviorPrefs rejects a
  // non-positive value back to the default, so the field is re-read from the
  // store rather than trusting what was typed.
  wireServerList();
  wireJlcPanel();
  document.getElementById('pref-reel-ceiling').addEventListener('change', (e) => {
    setBehaviorPrefs({ reelCeiling: Number(e.target.value) });
    e.target.value = String(getBehaviorPrefs().reelCeiling);
  });
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

  // The other shape of "heading with nothing under it": SECTION_HIERARCHY is
  // derived once from data/constants.json's SECTION_ORDER, so an empty one
  // renders an empty box and says nothing. Per the error policy, say it out
  // loud instead of leaving the user to guess whether the category colour
  // sliders are missing or merely collapsed. (The *layout* half of that
  // symptom is css/modals.css's .prefs-sliders flex-shrink:0.)
  if (!container.childElementCount) {
    AppLog.error(
      "Preferences: no category sliders to render — SECTION_ORDER from "
      + "data/constants.json parsed to an empty hierarchy."
    );
  }

  // Digikey sign-in status (no pairing code until the user asks for one)
  startDigikeyPanel();

  // Load Mouser API key status
  refreshMouserStatus();

  // Load Mirror status
  refreshMirrorStatus();

  // Paired JLC accounts (no pairing code until the user asks for one)
  startJlcPanel();

  // Sync keyboard prefs controls
  syncKeyboardPrefs();

  // Server roster + reachability dots (polls only while the modal is open)
  startServerList();

  prefsModal.open();
}

function refreshMirrorStatus() {
  var cb = document.getElementById("mirror-enabled");
  var urlEl = document.getElementById("mirror-url");
  var statusEl = document.getElementById("mirror-status");
  if (!cb || !urlEl || !statusEl) return;
  api("get_inventory_mirror_info").then(function (info) {
    if (!info) return;
    cb.checked = !!info.enabled;
    urlEl.value = info.serve_url || "";
    if (info.enabled && info.running) {
      statusEl.textContent = "Running" + (info.serve_url ? " — " + info.serve_url : "");
      statusEl.style.color = "var(--color-green)";
    } else if (info.enabled) {
      statusEl.textContent = "Enabled (daemon not running)";
      statusEl.style.color = "var(--color-amber, var(--text-muted))";
    } else {
      statusEl.textContent = "Disabled";
      statusEl.style.color = "var(--text-muted)";
    }
  });
}

function refreshMouserStatus() {
  var statusEl = document.getElementById("mouser-status");
  var keyInput = document.getElementById("mouser-api-key");
  var clearBtn = document.getElementById("mouser-clear");
  if (!statusEl || !keyInput || !clearBtn) return;
  api("get_mouser_api_key_status").then(function (result) {
    if (result && result.configured) {
      statusEl.textContent = "API key saved — using Mouser API";
      statusEl.style.color = "var(--color-green)";
      keyInput.value = "";
      keyInput.placeholder = "Replace key (leave empty to keep)";
      clearBtn.classList.remove("hidden");
    } else {
      statusEl.textContent = "No API key — falling back to web scrape";
      statusEl.style.color = "var(--text-muted)";
      keyInput.placeholder = "API key";
      clearBtn.classList.add("hidden");
    }
  });
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
  preferencesSignal.set(store.preferences);
}

// ── Digikey sign-in + Mouser key button wiring ──

export function wireDigikeyButtons() {
  wireDigikeyPanel();

  // Mouser API key save/clear
  var mouserSaveBtn = document.getElementById("mouser-save");
  var mouserClearBtn = document.getElementById("mouser-clear");
  var mouserKeyInput = document.getElementById("mouser-api-key");

  if (mouserSaveBtn && mouserKeyInput) {
    mouserSaveBtn.addEventListener("click", async () => {
      var key = mouserKeyInput.value.trim();
      if (!key) {
        showToast("Enter a Mouser API key first");
        return;
      }
      await api("set_mouser_api_key", key);
      refreshMouserStatus();
      showToast("Mouser API key saved");
      AppLog.info("Mouser API key configured");
    });
    mouserKeyInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); mouserSaveBtn.click(); }
    });
  }

  if (mouserClearBtn) {
    mouserClearBtn.addEventListener("click", async () => {
      await api("clear_mouser_api_key");
      refreshMouserStatus();
      showToast("Mouser API key cleared");
    });
  }

  // Mirror controls
  var mirrorCb = document.getElementById("mirror-enabled");
  if (mirrorCb) {
    mirrorCb.addEventListener("change", async () => {
      mirrorCb.disabled = true;
      try {
        if (mirrorCb.checked) {
          await api("enable_inventory_mirror");
          showToast("Inventory mirror enabled");
        } else {
          await api("disable_inventory_mirror");
          showToast("Inventory mirror disabled");
        }
      } catch (e) {
        showToast("Mirror error: " + (e && e.message ? e.message : e));
        AppLog.error("Mirror toggle failed: " + e);
      } finally {
        mirrorCb.disabled = false;
        refreshMirrorStatus();
      }
    });
  }
  var mirrorCopy = document.getElementById("mirror-copy");
  if (mirrorCopy) {
    mirrorCopy.addEventListener("click", () => {
      var u = document.getElementById("mirror-url");
      if (u && u.value) { navigator.clipboard.writeText(u.value); showToast("Copied"); }
    });
  }
}

// ── Module init ──
wireKeyboardPrefs();
