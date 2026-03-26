/* bom/bom-panel.js — Thin wiring layer for the BOM panel.
   Imports pure logic and renderer, wires up DOM events and EventBus. */

import { EventBus, Events } from '../event-bus.js';
import { api, AppLog } from '../api.js';
import { showToast, escHtml, Modal, setupDropZone, resetDropZoneInput } from '../ui-helpers.js';
import { UndoRedo } from '../undo-redo.js';
import { App, snapshotLinks, onInventoryUpdated, savePreferences } from '../store.js';
import { bomKey, invPartKey, countStatuses, bomAggKey, rawRowAggKey } from '../part-keys.js';
import { processBOM, aggregateBomRows, generateCSV } from '../csv-parser.js';
import { matchBOM } from '../matching.js';
import { classifyBomRow, countBomWarnings, computeRows, buildStatusMap, buildLinkableKeys, prepareConsumption, computePriceInfo } from './bom-logic.js';
import { renderDropZone, renderLoadedDropZone, renderBomSummary, renderPriceInfo, renderLinkingBanner, renderStagingHead, renderStagingRow } from './bom-renderer.js';

const body = document.getElementById("bom-body");
let lastResults = null;
let lastFileName = "";

// Editable raw-row state
let bomRawRows = [];
let bomHeaders = [];
let bomCols = {};
let bomDirty = false;

function updateSaveBtnState() {
  const btn = document.getElementById("bom-save-btn");
  if (btn) btn.classList.toggle("dirty", bomDirty);
}

// -- Aggregate from in-memory raw rows --

function aggregateFromRawRows() {
  return aggregateBomRows(bomRawRows, bomHeaders, bomCols).aggregated;
}

// -- Single source of truth: re-derive everything from raw rows --

function reprocessAndRender() {
  const aggregated = aggregateFromRawRows();
  const results = matchBOM(aggregated, App.inventory, App.links.manualLinks, App.links.confirmedMatches);
  lastResults = results;
  App.bomResults = results;
  App.bomHeaders = bomHeaders;
  App.bomCols = bomCols;
  emitBomData();
}

// -- Multiplier --

function getMultiplier() {
  const el = document.getElementById("bom-qty-mult");
  return el ? Math.max(1, parseInt(el.value, 10) || 1) : 1;
}

// -- Compute effective rows and emit to inventory panel --

function emitBomData() {
  const rows = computeRows(lastResults, getMultiplier(), App.links);
  if (!rows) return;
  renderBomPanel(rows);
  EventBus.emit(Events.BOM_LOADED, { rows, fileName: lastFileName, multiplier: getMultiplier() });
}

// -- Render the BOM panel (editable raw rows + summary) --

function renderBomPanel(rows) {
  const mult = getMultiplier();
  const c = countStatuses(rows);

  // Summary
  document.getElementById("bom-summary").innerHTML = renderBomSummary(c, lastFileName, mult);

  // Price info
  const { pricePerBoard, totalPrice } = computePriceInfo(rows, mult);
  const priceInfo = document.getElementById("bom-price-info");
  if (priceInfo) priceInfo.textContent = renderPriceInfo(pricePerBoard, totalPrice, mult);

  // Linking banner
  const bannerEl = document.getElementById("linking-banner");
  if (bannerEl) bannerEl.remove();
  const bannerHtml = renderLinkingBanner({
    active: App.links.linkingMode,
    invItem: App.links.linkingInvItem,
    bomRow: App.links.linkingBomRow,
  });
  if (bannerHtml) {
    const resultsEl = document.getElementById("bom-results");
    const tableWrap = resultsEl.querySelector(".bom-table-wrap");
    const temp = document.createElement("div");
    temp.innerHTML = bannerHtml;
    const banner = temp.firstElementChild;
    banner.querySelector(".cancel-link-btn").addEventListener("click", () => {
      if (App.links.linkingInvItem) App.links.setLinkingMode(false);
      else App.links.setReverseLinkingMode(false);
    });
    if (tableWrap) resultsEl.insertBefore(banner, tableWrap);
  }

  // Staging toolbar title
  const warnCount = countBomWarnings(bomRawRows, bomCols);
  const stagingTitle = document.getElementById("bom-staging-title");
  if (stagingTitle) {
    stagingTitle.textContent = "Staging (" + bomRawRows.length + " rows" + (warnCount > 0 ? ", " + warnCount + " warnings" : "") + ")";
  }

  // Build status + linking maps
  const statusMap = buildStatusMap(rows);
  const missingKeys = (App.links.linkingMode && App.links.linkingInvItem)
    ? buildLinkableKeys(rows, true)
    : new Set();

  // Render staging table
  document.getElementById("bom-thead").innerHTML = renderStagingHead(bomHeaders);

  const tbody = document.getElementById("bom-tbody");
  let tbodyHtml = "";
  bomRawRows.forEach((row, ri) => {
    const cls = classifyBomRow(row, bomCols);
    const rk = rawRowAggKey(row, bomCols);
    const st = (cls === "ok" && rk) ? (statusMap[rk] || null) : null;
    const isLinkTarget = !!(App.links.linkingMode && App.links.linkingInvItem && cls === "ok" && rk && missingKeys.has(rk));
    tbodyHtml += renderStagingRow(row, ri, bomCols, bomHeaders, st, isLinkTarget, cls);
  });
  tbody.innerHTML = tbodyHtml;

  document.getElementById("bom-results").classList.remove("hidden");
}

// -- Drop Zone --

async function browseBomFile() {
  const result = await api("open_file_dialog", "Select BOM CSV", App.preferences.lastBomDir || null);
  if (!result || !result.content) return;
  if (result.directory) {
    App.preferences.lastBomDir = result.directory;
  }
  if (result.path) {
    App.preferences.lastBomFile = result.path;
  }
  savePreferences();
  loadBomText(result.content, result.name, result.links || null);
}

function handleFile(file) {
  AppLog.info("Reading BOM file: " + file.name);
  const reader = new FileReader();
  reader.onload = () => {
    const bytes = new Uint8Array(reader.result);
    let text;
    if (bytes.length >= 2 && bytes[0] === 0xFF && bytes[1] === 0xFE) {
      text = new TextDecoder("utf-16le").decode(bytes);
    } else if (bytes.length >= 2 && bytes[0] === 0xFE && bytes[1] === 0xFF) {
      text = new TextDecoder("utf-16be").decode(bytes);
    } else {
      text = new TextDecoder("utf-8").decode(bytes);
    }
    loadBomText(text, file.name);
  };
  reader.readAsArrayBuffer(file);
}

function loadBomText(text, fileName, savedLinks) {
  const result = processBOM(text, fileName);
  if (!result) {
    showToast("Could not parse BOM \u2014 too few lines");
    AppLog.error("BOM parse failed: too few lines in " + fileName);
    return;
  }
  const { headers, cols, rawRows, aggregated, warnings } = result;

  if (aggregated.size === 0) {
    showToast("No parts found in BOM");
    AppLog.error("BOM has no valid parts: " + fileName);
    return;
  }

  // Store raw state
  bomHeaders = headers;
  bomCols = cols;
  bomRawRows = rawRows.map(r => r.slice()); // shallow copy each row

  App.bomHeaders = headers;
  App.bomCols = cols;
  App.links.loadFromSaved(savedLinks);

  // Log warnings
  warnings.forEach(w => {
    AppLog.warn("BOM row " + (w.ri + 1) + ": " + w.msg);
  });

  // Match
  const results = matchBOM(aggregated, App.inventory, App.links.manualLinks, App.links.confirmedMatches);
  lastResults = results;
  lastFileName = fileName;
  App.bomResults = results;
  App.bomFileName = fileName;

  // Log summary
  const matched = results.filter(r => r.inv).length;
  const missing = results.filter(r => !r.inv).length;
  AppLog.info("BOM loaded: " + fileName + " \u2014 " + rawRows.length + " rows, " + aggregated.size + " unique, " + matched + " matched, " + missing + " missing");

  emitBomData();

  const zone = document.getElementById("bom-drop-zone");
  zone.innerHTML = renderLoadedDropZone(fileName);
  zone.classList.add("loaded");
  resetDropZoneInput("bom-file-input", handleFile);

  document.getElementById("bom-save-btn").disabled = false;
  document.getElementById("bom-consume-btn").disabled = false;
  document.getElementById("bom-clear-btn").disabled = false;
}

// -- Manual Linking --

function createManualLink(bomRow) {
  const bk = bomKey(bomRow.bom);
  const ipk = invPartKey(App.links.linkingInvItem);
  if (!bk || !ipk) {
    showToast("Cannot create link \u2014 missing part key");
    return;
  }
  UndoRedo.save("links", snapshotLinks());
  App.links.addManualLink(bk, ipk);
  AppLog.info("Manual link: " + ipk + " \u2192 " + bk);
  App.links.setLinkingMode(false);
  showToast("Linked " + ipk + " \u2192 " + bk);
}

// -- Consume --

let consumeArmed = false;
let lastConsumeMeta = null;

function resetConsumeConfirm() {
  consumeArmed = false;
  const btn = document.getElementById("consume-confirm");
  if (btn) {
    btn.textContent = "Consume";
    btn.classList.remove("btn-danger");
    btn.classList.add("btn-apply");
  }
}

const consumeModal = Modal("consume-modal", {
  onClose: () => resetConsumeConfirm(),
  cancelId: "consume-cancel",
});

function openConsumeModal() {
  AppLog.info("Opening consume modal");
  const mult = getMultiplier();
  const matched = lastResults.filter(r => r.inv && r.matchType !== "value" && r.matchType !== "fuzzy");
  document.getElementById("consume-subtitle").textContent =
    `Consume ${matched.length} matched parts x${mult} from "${lastFileName}"?`;
  document.getElementById("consume-note").value = "";
  resetConsumeConfirm();
  consumeModal.open();
}

// -- Init --

export function init() {
  body.innerHTML = renderDropZone();
  setupDropZone("bom-drop-zone", "bom-file-input", browseBomFile, handleFile);

  // Multiplier input
  body.addEventListener("input", (e) => {
    if (e.target.id === "bom-qty-mult") {
      AppLog.info("BOM multiplier set to " + (parseInt(e.target.value, 10) || 1));
      emitBomData();
    }
  });

  // Save BOM
  body.addEventListener("click", async (e) => {
    if (e.target.id === "bom-save-btn") {
      if (!bomHeaders.length || !bomRawRows.length) return;
      const csvText = generateCSV(bomHeaders, bomRawRows);
      const linksJson = App.links.hasLinks() ? JSON.stringify({
        manualLinks: App.links.manualLinks,
        confirmedMatches: App.links.confirmedMatches,
      }) : null;
      const result = await api("save_file_dialog", csvText, lastFileName || "bom.csv", App.preferences.lastBomDir || null, linksJson);
      if (result && result.path) {
        bomDirty = false;
        App.bomDirty = false;
        api("set_bom_dirty", false);
        updateSaveBtnState();
        App.preferences.lastBomFile = result.path;
        savePreferences();
        showToast("Saved BOM to " + result.path);
        AppLog.info("Saved BOM: " + result.path);
      }
    }
  });

  // Clear BOM
  body.addEventListener("click", (e) => {
    if (e.target.id === "bom-clear-btn") {
      lastResults = null;
      lastFileName = "";
      bomRawRows = [];
      bomHeaders = [];
      bomCols = {};
      bomDirty = false;
      App.bomDirty = false;
      api("set_bom_dirty", false);
      App.bomResults = null;
      App.bomFileName = "";
      App.preferences.lastBomFile = null;
      savePreferences();
      document.getElementById("bom-results").classList.add("hidden");
      document.getElementById("bom-thead").innerHTML = "";
      document.getElementById("bom-tbody").innerHTML = "";
      const zone = document.getElementById("bom-drop-zone");
      zone.innerHTML = `<p>Drop a BOM CSV here, or click to browse</p>
        <div class="hint">Supports JLCPCB, KiCad, and generic BOM formats</div>
        <input type="file" id="bom-file-input" accept=".csv,.tsv,.txt">`;
      zone.classList.remove("loaded");
      resetDropZoneInput("bom-file-input", handleFile);
      AppLog.info("BOM cleared");
      EventBus.emit(Events.BOM_CLEARED);
    }
  });

  // Consume button
  body.addEventListener("click", (e) => {
    if (e.target.id === "bom-consume-btn") {
      if (!lastResults || !lastFileName) return;
      openConsumeModal();
    }
  });

  // Consume confirm
  document.getElementById("consume-confirm").addEventListener("click", async () => {
    if (!consumeArmed) {
      consumeArmed = true;
      const btn = document.getElementById("consume-confirm");
      btn.textContent = "Are you sure?";
      btn.classList.remove("btn-apply");
      btn.classList.add("btn-danger");
      return;
    }
    resetConsumeConfirm();
    if (!lastResults || !lastFileName) return;
    const mult = getMultiplier();
    const note = document.getElementById("consume-note").value;

    const { matches, matchesJson } = prepareConsumption(lastResults);

    if (matches.length === 0) {
      showToast("No matched parts to consume");
      AppLog.warn("Consume cancelled: no matched parts");
      consumeModal.close();
      return;
    }

    UndoRedo.save("consume", {
      _undoType: "consume",
      adjustmentCount: matches.length,
      matchesJson: matchesJson,
      mult: mult,
      bomName: lastFileName,
      note: note,
    });

    const fresh = await api("consume_bom", matchesJson, mult, lastFileName, note);
    if (!fresh) {
      UndoRedo.popLast();
      return;
    }
    lastConsumeMeta = {
      matchesJson: matchesJson,
      mult: mult,
      bomName: lastFileName,
      note: note,
      adjustmentCount: matches.length,
    };
    consumeModal.close();
    onInventoryUpdated(fresh);
    showToast(`Consumed ${matches.length} parts x${mult}`);
    AppLog.info("Consumed " + matches.length + " parts x" + mult + " from " + lastFileName);
  });

  // Event delegation on staging tbody for row interactions
  const tbodyEl = document.getElementById("bom-tbody");

  tbodyEl.addEventListener("click", (e) => {
    // Delete button
    const deleteTarget = e.target.closest('[data-action="delete"]');
    if (deleteTarget) {
      const ri = parseInt(deleteTarget.dataset.ri, 10);
      UndoRedo.save("bom", bomRawRows);
      bomRawRows.splice(ri, 1);
      bomDirty = true;
      App.bomDirty = true;
      api("set_bom_dirty", true);
      updateSaveBtnState();
      AppLog.info("Deleted BOM row " + (ri + 1));
      reprocessAndRender();
      return;
    }

    // Link target click
    const linkTarget = e.target.closest('[data-action="link"]');
    if (linkTarget) {
      const aggKey = linkTarget.dataset.aggKey;
      const rows = computeRows(lastResults, getMultiplier(), App.links);
      if (rows) {
        const matchedResult = rows.find(r => bomAggKey(r.bom) === aggKey);
        if (matchedResult) createManualLink(matchedResult);
      }
      return;
    }

    // Refs cell click-to-edit
    const refsCell = e.target.closest('[data-action="show-input"]');
    if (refsCell) {
      e.stopPropagation();
      const td = refsCell.parentElement;
      const inp = td.querySelector("input");
      if (inp) {
        refsCell.style.display = "none";
        inp.style.display = "";
        inp.focus();
        inp.select();
      }
      return;
    }
  });

  // Event delegation for input changes (edits)
  tbodyEl.addEventListener("change", (e) => {
    if (e.target.tagName === "INPUT") {
      const tr = e.target.closest("tr");
      const ri = parseInt(tr.dataset.ri, 10);
      const ci = parseInt(e.target.dataset.ci, 10);
      UndoRedo.save("bom", bomRawRows);
      bomRawRows[ri][ci] = e.target.value;
      bomDirty = true;
      App.bomDirty = true;
      api("set_bom_dirty", true);
      updateSaveBtnState();
      AppLog.info("Edited BOM cell [" + (ri + 1) + ", " + ci + "]");
      reprocessAndRender();
    }
  });

  // Event delegation for refs input blur (restore colorized display)
  tbodyEl.addEventListener("focusout", (e) => {
    if (e.target.tagName === "INPUT") {
      const td = e.target.parentElement;
      const display = td.querySelector(".refs-cell");
      if (display) {
        display.innerHTML = escHtml(e.target.value).split(/,\s*/).map(r => r).join(", ");
        // Re-render to get proper colorization
        reprocessAndRender();
      }
    }
  });

  // Undo/redo registrations
  UndoRedo.register("bom", (action, data) => {
    if (action === "snapshot") return JSON.parse(JSON.stringify(bomRawRows));
    bomRawRows = data;
    reprocessAndRender();
  });

  UndoRedo.register("consume", async (action, data) => {
    if (action === "snapshot") {
      if (lastConsumeMeta) {
        return { _undoType: "consume-done", ...lastConsumeMeta };
      }
      return { _undoType: "consume-none" };
    }
    if (data._undoType === "consume") {
      const fresh = await api("remove_last_adjustments", data.adjustmentCount);
      if (!fresh) throw new Error("Failed to undo consume");
      lastConsumeMeta = null;
      onInventoryUpdated(fresh);
      showToast("Undid consume of " + data.adjustmentCount + " parts");
    } else if (data._undoType === "consume-done") {
      const fresh = await api("consume_bom", data.matchesJson, data.mult, data.bomName, data.note);
      if (!fresh) throw new Error("Failed to redo consume");
      lastConsumeMeta = {
        matchesJson: data.matchesJson,
        mult: data.mult,
        bomName: data.bomName,
        note: data.note,
        adjustmentCount: data.adjustmentCount,
      };
      onInventoryUpdated(fresh);
      showToast("Redid consume of " + data.adjustmentCount + " parts");
    }
  });

  // EventBus subscriptions
  EventBus.on(Events.INVENTORY_UPDATED, () => {
    if (lastResults && lastFileName && bomRawRows.length) {
      reprocessAndRender();
    }
  });

  EventBus.on(Events.CONFIRMED_CHANGED, () => {
    if (lastResults && lastFileName && bomRawRows.length) reprocessAndRender();
  });

  EventBus.on(Events.LINKS_CHANGED, () => {
    if (lastResults && lastFileName && bomRawRows.length) reprocessAndRender();
  });

  EventBus.on(Events.LINKING_MODE, () => {
    if (lastResults) {
      const rows = computeRows(lastResults, getMultiplier(), App.links);
      if (rows) renderBomPanel(rows);
    }
  });

  EventBus.on(Events.SAVE_AND_CLOSE, async () => {
    if (!bomHeaders.length || !bomRawRows.length) {
      api("confirm_close");
      return;
    }
    const csvText = generateCSV(bomHeaders, bomRawRows);
    const linksJson = App.links.hasLinks() ? JSON.stringify({
      manualLinks: App.links.manualLinks,
      confirmedMatches: App.links.confirmedMatches,
    }) : null;
    const result = await api("save_file_dialog", csvText, lastFileName || "bom.csv", App.preferences.lastBomDir || null, linksJson);
    if (result && result.path) {
      bomDirty = false;
      App.bomDirty = false;
      api("set_bom_dirty", false);
      App.preferences.lastBomFile = result.path;
      await savePreferences();
    }
    api("confirm_close");
  });

  EventBus.on(Events.INVENTORY_LOADED, async () => {
    const lastFile = App.preferences.lastBomFile;
    if (!lastFile) return;
    const result = await api("load_file", lastFile);
    if (result && result.content) {
      loadBomText(result.content, result.name, result.links || null);
      AppLog.info("Auto-loaded last BOM: " + result.name);
    } else if (lastFile) {
      AppLog.warn("Could not auto-load last BOM: " + lastFile);
    }
  });
}
