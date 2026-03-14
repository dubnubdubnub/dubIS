// @ts-check
/* openpnp-modal.js — OpenPnP part metadata modal with footprint canvas viewer */

import { EventBus, Events } from './event-bus.js';
import { api, AppLog } from './api.js';
import { showToast, Modal } from './ui-helpers.js';
import { App } from './store.js';

const modal = Modal("openpnp-modal", { cancelId: "openpnp-cancel" });

/** @type {string} Part key being edited */
let currentPartKey = "";
/** @type {string} LCSC number (for fetching) */
let currentLcsc = "";
/** @type {string} Full KiCad footprint ref (for KiCad lib fetch) */
let currentFpRef = "";
/** @type {object|null} Current footprint data */
let currentFootprint = null;

// ── Open modal ─────────────────────────────────────────────────────────

/**
 * Open the OpenPnP modal for a given part.
 * @param {string} partKey
 * @param {string} [lcsc]
 * @param {string} [fpRef] Full KiCad footprint reference
 */
export function openOpenpnpModal(partKey, lcsc, fpRef) {
  currentPartKey = partKey;
  currentLcsc = lcsc || "";
  currentFpRef = fpRef || "";

  const meta = App.openpnpParts[partKey] || {};

  document.getElementById("openpnp-modal-title").textContent = `OpenPnP: ${partKey}`;
  document.getElementById("openpnp-modal-subtitle").textContent = lcsc ? `LCSC: ${lcsc}` : "";

  /** @type {HTMLInputElement} */ (document.getElementById("openpnp-package-id")).value = meta.package_id || "";
  /** @type {HTMLInputElement} */ (document.getElementById("openpnp-part-id")).value = meta.openpnp_id || partKey;
  /** @type {HTMLInputElement} */ (document.getElementById("openpnp-height")).value = String(meta.height || 0);
  /** @type {HTMLInputElement} */ (document.getElementById("openpnp-speed")).value = String(meta.speed || 1.0);

  // Nozzle tips
  renderNozzleTips(meta.nozzle_tips || []);

  // Footprint
  currentFootprint = meta.footprint || null;
  updateFootprintStatus();
  renderFootprint();

  // EasyEDA button visibility
  const easyedaBtn = document.getElementById("openpnp-fetch-easyeda");
  if (easyedaBtn) {
    easyedaBtn.style.display = (currentLcsc && currentLcsc.match(/^C\d+$/)) ? "" : "none";
  }

  modal.open();
}

// ── Nozzle tips ────────────────────────────────────────────────────────

function renderNozzleTips(selected) {
  const container = document.getElementById("openpnp-nozzle-tips");
  if (!container) return;
  const tips = App.openpnpNozzleTips;
  if (tips.length === 0) {
    container.innerHTML = '<span style="font-size:11px;color:var(--text-muted)">No nozzle tips configured</span>';
    return;
  }
  container.innerHTML = tips.map(tip => {
    const checked = selected.includes(tip.id) ? " checked" : "";
    return `<label><input type="checkbox" value="${tip.id}"${checked}> ${tip.name || tip.id}</label>`;
  }).join("");
}

function getSelectedNozzleTips() {
  const container = document.getElementById("openpnp-nozzle-tips");
  if (!container) return [];
  return Array.from(container.querySelectorAll("input:checked")).map(
    /** @param {HTMLInputElement} el */ el => el.value
  );
}

// ── Footprint status ───────────────────────────────────────────────────

function updateFootprintStatus() {
  const el = document.getElementById("openpnp-fp-status");
  if (!el) return;
  if (currentFootprint && currentFootprint.pads && currentFootprint.pads.length > 0) {
    const src = currentFootprint._source || "loaded";
    el.textContent = `Footprint: ${currentFootprint.pads.length} pads (${src})`;
    el.style.color = "var(--color-green)";
  } else {
    el.textContent = "No footprint";
    el.style.color = "var(--text-muted)";
  }
}

// ── Footprint canvas renderer ──────────────────────────────────────────

function renderFootprint() {
  const canvas = /** @type {HTMLCanvasElement} */ (document.getElementById("openpnp-fp-canvas"));
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  if (!currentFootprint || !currentFootprint.pads || currentFootprint.pads.length === 0) {
    ctx.fillStyle = "#484f58";
    ctx.font = "12px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("No footprint data", w / 2, h / 2);
    return;
  }

  const pads = currentFootprint.pads;
  const bodyW = currentFootprint.body_width || 1;
  const bodyH = currentFootprint.body_height || 1;

  // Calculate extent for auto-scaling
  let extentW = bodyW, extentH = bodyH;
  for (const p of pads) {
    const px = Math.abs(p.x) + p.width / 2;
    const py = Math.abs(p.y) + p.height / 2;
    extentW = Math.max(extentW, px * 2);
    extentH = Math.max(extentH, py * 2);
  }

  const margin = 20;
  const scale = Math.min((w - margin * 2) / extentW, (h - margin * 2) / extentH);
  const ox = w / 2;
  const oy = h / 2;

  // Draw mm grid
  ctx.strokeStyle = "#21262d";
  ctx.lineWidth = 0.5;
  const gridMm = 0.5;
  const gridPx = gridMm * scale;
  if (gridPx > 4) {
    for (let gx = ox % gridPx; gx < w; gx += gridPx) {
      ctx.beginPath(); ctx.moveTo(gx, 0); ctx.lineTo(gx, h); ctx.stroke();
    }
    for (let gy = oy % gridPx; gy < h; gy += gridPx) {
      ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(w, gy); ctx.stroke();
    }
  }

  // Draw body outline
  ctx.strokeStyle = "#484f58";
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 3]);
  ctx.strokeRect(ox - bodyW / 2 * scale, oy - bodyH / 2 * scale, bodyW * scale, bodyH * scale);
  ctx.setLineDash([]);

  // Draw pads
  for (let i = 0; i < pads.length; i++) {
    const p = pads[i];
    const px = ox + p.x * scale;
    const py = oy + p.y * scale;
    const pw = p.width * scale;
    const ph = p.height * scale;
    const roundness = p.roundness || 0;

    // Pad 1 gets a distinct color for polarity
    ctx.fillStyle = i === 0 ? "#f85149" : "#58a6ff";
    ctx.globalAlpha = 0.8;

    if (roundness >= 100) {
      // Circle
      ctx.beginPath();
      ctx.arc(px, py, Math.max(pw, ph) / 2, 0, Math.PI * 2);
      ctx.fill();
    } else if (roundness >= 40) {
      // Rounded rect (oval-ish)
      const r = Math.min(pw, ph) * 0.4;
      drawRoundRect(ctx, px - pw / 2, py - ph / 2, pw, ph, r);
      ctx.fill();
    } else {
      // Rectangle
      ctx.fillRect(px - pw / 2, py - ph / 2, pw, ph);
    }

    // Pad label
    ctx.globalAlpha = 1;
    ctx.fillStyle = "#e6edf3";
    ctx.font = `${Math.max(8, Math.min(11, pw * 0.7))}px sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    if (pw > 6 && ph > 6) {
      ctx.fillText(String(p.name), px, py);
    }
  }

  ctx.globalAlpha = 1;

  // Crosshair at origin
  ctx.strokeStyle = "#636c76";
  ctx.lineWidth = 0.5;
  ctx.beginPath(); ctx.moveTo(ox - 6, oy); ctx.lineTo(ox + 6, oy); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(ox, oy - 6); ctx.lineTo(ox, oy + 6); ctx.stroke();
}

/**
 * @param {CanvasRenderingContext2D} ctx
 * @param {number} x
 * @param {number} y
 * @param {number} w
 * @param {number} h
 * @param {number} r
 */
function drawRoundRect(ctx, x, y, w, h, r) {
  r = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

// ── Fetch footprint buttons ────────────────────────────────────────────

document.getElementById("openpnp-fetch-easyeda")?.addEventListener("click", async () => {
  if (!currentLcsc) return;
  AppLog.info(`Fetching EasyEDA footprint for ${currentLcsc}...`);
  const fp = await api("fetch_footprint", currentLcsc);
  if (fp) {
    currentFootprint = { ...fp, _source: "easyeda" };
    updateFootprintStatus();
    renderFootprint();
    showToast("Fetched footprint from EasyEDA");
  }
});

document.getElementById("openpnp-fetch-kicad")?.addEventListener("click", async () => {
  if (!currentFpRef) {
    // Try to get from expanded parts
    const ref = prompt("Enter KiCad footprint reference (e.g. Resistor_SMD:R_0402_1005Metric):");
    if (!ref) return;
    currentFpRef = ref;
  }
  AppLog.info(`Fetching KiCad footprint: ${currentFpRef}...`);
  const fp = await api("fetch_kicad_footprint", currentFpRef);
  if (fp) {
    currentFootprint = { ...fp, _source: "kicad" };
    updateFootprintStatus();
    renderFootprint();
    showToast("Loaded footprint from KiCad library");
  }
});

// ── Save / Delete / Sync ───────────────────────────────────────────────

document.getElementById("openpnp-save")?.addEventListener("click", async () => {
  const data = {
    part_key: currentPartKey,
    openpnp_id: /** @type {HTMLInputElement} */ (document.getElementById("openpnp-part-id")).value.trim() || currentPartKey,
    package_id: /** @type {HTMLInputElement} */ (document.getElementById("openpnp-package-id")).value.trim(),
    height: parseFloat(/** @type {HTMLInputElement} */ (document.getElementById("openpnp-height")).value) || 0,
    speed: parseFloat(/** @type {HTMLInputElement} */ (document.getElementById("openpnp-speed")).value) || 1.0,
    nozzle_tips: getSelectedNozzleTips(),
    footprint: currentFootprint,
    footprint_source: currentFootprint?._source || "manual",
    footprint_fetched: currentFootprint ? new Date().toISOString() : "",
  };

  const result = await api("save_openpnp_part", JSON.stringify(data));
  if (result) {
    App.openpnpParts = result.parts || {};
    modal.close();
    EventBus.emit(Events.OPENPNP_PARTS_CHANGED);
    showToast(`Saved OpenPnP data for ${currentPartKey}`);
  }
});

document.getElementById("openpnp-delete")?.addEventListener("click", async () => {
  if (!currentPartKey) return;
  const result = await api("remove_openpnp_part", currentPartKey);
  if (result) {
    App.openpnpParts = result.parts || {};
    modal.close();
    EventBus.emit(Events.OPENPNP_PARTS_CHANGED);
    showToast(`Removed OpenPnP data for ${currentPartKey}`);
  }
});

document.getElementById("openpnp-sync")?.addEventListener("click", async () => {
  const result = await api("sync_openpnp");
  if (result) {
    showToast("Synced to OpenPnP");
    AppLog.info(`Wrote ${result.packages_xml} and ${result.parts_xml}`);
  }
});

// ── Listen for open requests from kicad-panel ──────────────────────────

EventBus.on(Events.OPENPNP_PARTS_CHANGED, (data) => {
  if (data && data.action === "open") {
    openOpenpnpModal(data.partKey, data.lcsc, data.fpRef);
  }
});
