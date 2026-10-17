/* bom/bom-panel.js — Thin wiring layer for the BOM panel.
   Imports pure logic and renderer, wires up DOM events and EventBus. */

import { EventBus, Events } from '../event-bus.js';
import { api, apiRequest, AppLog } from '../api.js';
import { showToast, Modal, setupDropZone, resetDropZoneInput } from '../ui-helpers.js';
import { UndoRedo } from '../undo-redo.js';
import { store, setBomResults, setBomFootprintNearMisses, setBomMeta, snapshotLinks, savePreferences, publishPreferences } from '../store.js';
import { bomKey, invPartKey, countStatuses, rawRowAggKey } from '../part-keys.js';
import { processBOM, aggregateBomRows } from '../csv-parser.js';
import { matchBOM } from '../matching.js';
import { classifyBomRow, countBomWarnings, computeRows, buildStatusMap, buildLinkableKeys, computePriceInfo, prepareConsumption } from './bom-logic.js';
import { renderDropZone, renderLoadedDropZone, renderBomSummary, renderPriceInfo, renderLinkingBanner, renderStagingHead, renderStagingRow } from './bom-renderer.js';
import state from './bom-state.js';
import { setupEvents } from './bom-events.js';
import { createSpecLookup, codesNeedingSpec } from './bom-spec-lookup.js';

// -- Helpers --

function updateSaveBtnState() {
  const btn = document.getElementById("bom-save-btn");
  if (btn) btn.classList.toggle("dirty", state.bomDirty);
}

function aggregateFromRawRows() {
  return aggregateBomRows(state.bomRawRows, state.bomHeaders, state.bomCols).aggregated;
}

// -- Distributor specs for unidentified rows --
// One lookup per window session: the cache is keyed by LCSC code, so a
// re-match (an edit, an inventory refresh, a link) re-applies what is already
// known instead of asking again. See js/bom/bom-spec-lookup.js.
const specLookup = createSpecLookup({
  fetchProduct: (code) => apiRequest("fetch_lcsc_product", code),
  log: AppLog,
});

/** Every match goes through here, so no path can forget the cached specs. */
function runMatch(aggregated) {
  specLookup.apply(aggregated);
  return matchBOM(aggregated, store.inventory, store.links.manualLinks, store.links.confirmedMatches, store.genericParts);
}

/** A staging cell is mid-edit: re-rendering the tbody now would discard the typing. */
function editingStagingCell() {
  const a = document.activeElement;
  return !!(a && a.tagName === "INPUT" && a.closest("#bom-tbody"));
}

/**
 * Ask LCSC about the rows the last match could not identify, and re-match if
 * anything was learned. Fire-and-forget from every match; terminates because
 * a re-match only follows a fetch that FOUND something, and a found code is
 * cached for the session, so the re-match's own call asks nothing.
 */
async function refreshSpecs() {
  const codes = codesNeedingSpec(state.lastResults);
  if (codes.length === 0) return;
  const outcome = await specLookup.lookup(codes);
  if (outcome.found.length === 0) return;
  // The BOM may have been cleared while we waited; nothing to upgrade then.
  if (!state.lastResults || !state.bomRawRows.length) return;
  // The focusout handler re-matches when the edit ends, and that match applies
  // the cache — so skipping here loses nothing.
  if (editingStagingCell()) return;
  AppLog.info("BOM spec lookup: learned " + outcome.found.length + " LCSC spec(s); re-matching");
  reprocessAndRender({ passive: true });
}

function kickSpecLookup() {
  refreshSpecs().catch((e) => AppLog.warn("BOM spec lookup crashed: " + (e && e.message ? e.message : e)));
}

/**
 * @param {{passive?: boolean}} [opts] `passive`: nobody asked for this
 *   re-match (an inventory refresh, a spec lookup landing), so announce it as
 *   BOM_REMATCHED — which does not force a collapsed BOM panel open.
 */
function reprocessAndRender(opts = {}) {
  const aggregated = aggregateFromRawRows();
  const { results, footprintNearMisses } = runMatch(aggregated);
  state.lastResults = results;
  setBomResults(results);
  setBomFootprintNearMisses(footprintNearMisses);
  setBomMeta({ headers: state.bomHeaders, cols: state.bomCols });
  emitBomData(!!opts.passive);
  kickSpecLookup();
}

// -- Multiplier --

function getMultiplier() {
  const el = document.getElementById("bom-qty-mult");
  return el ? Math.max(1, parseInt(el.value, 10) || 1) : 1;
}

// -- Compute effective rows and emit to inventory panel --

function emitBomData(passive = false) {
  const rows = computeRows(state.lastResults, getMultiplier(), store.links);
  if (!rows) return;
  renderBomPanel(rows);
  const payload = { rows, fileName: state.lastFileName, multiplier: getMultiplier() };
  // Two literal emits, not a ternary: tests/js/event-bus-contract.test.js reads
  // literal emit calls to prove every event is both emitted and heard.
  if (passive) EventBus.emit(Events.BOM_REMATCHED, payload);
  else EventBus.emit(Events.BOM_LOADED, payload);
}

// -- Render the BOM panel (editable raw rows + summary) --

function renderBomPanel(rows) {
  const mult = getMultiplier();
  const c = countStatuses(rows);

  // Summary
  document.getElementById("bom-summary").innerHTML = renderBomSummary(c, state.lastFileName, mult);

  // Price info
  const { pricePerBoard, totalPrice } = computePriceInfo(rows, mult);
  const priceInfo = document.getElementById("bom-price-info");
  if (priceInfo) priceInfo.textContent = renderPriceInfo(pricePerBoard, totalPrice, mult);

  // Linking banner
  const bannerEl = document.getElementById("linking-banner");
  if (bannerEl) bannerEl.remove();
  const bannerHtml = renderLinkingBanner({
    active: store.links.linkingMode,
    invItem: store.links.linkingInvItem,
    bomRow: store.links.linkingBomRow,
  });
  if (bannerHtml) {
    const resultsEl = document.getElementById("bom-results");
    const tableWrap = resultsEl.querySelector(".bom-table-wrap");
    const temp = document.createElement("div");
    temp.innerHTML = bannerHtml;
    const banner = temp.firstElementChild;
    banner.querySelector(".cancel-link-btn").addEventListener("click", () => {
      if (store.links.linkingInvItem) store.links.setLinkingMode(false);
      else store.links.setReverseLinkingMode(false);
    });
    if (tableWrap) resultsEl.insertBefore(banner, tableWrap);
  }

  // Staging toolbar title
  const warnCount = countBomWarnings(state.bomRawRows, state.bomCols);
  const stagingTitle = document.getElementById("bom-staging-title");
  if (stagingTitle) {
    stagingTitle.textContent = "Staging (" + state.bomRawRows.length + " rows" + (warnCount > 0 ? ", " + warnCount + " warnings" : "") + ")";
  }

  // Build status + linking maps
  const statusMap = buildStatusMap(rows);
  const missingKeys = (store.links.linkingMode && store.links.linkingInvItem)
    ? buildLinkableKeys(rows, true)
    : new Set();

  // Render staging table
  document.getElementById("bom-thead").innerHTML = renderStagingHead(state.bomHeaders, state.bomCols);

  const tbody = document.getElementById("bom-tbody");
  let tbodyHtml = "";
  state.bomRawRows.forEach((row, ri) => {
    const cls = classifyBomRow(row, state.bomCols);
    const rk = rawRowAggKey(row, state.bomCols);
    const st = (cls === "ok" && rk) ? (statusMap[rk] || null) : null;
    const isLinkTarget = !!(store.links.linkingMode && store.links.linkingInvItem && cls === "ok" && rk && missingKeys.has(rk));
    tbodyHtml += renderStagingRow(row, ri, state.bomCols, state.bomHeaders, st, isLinkTarget, cls);
  });
  tbody.innerHTML = tbodyHtml;

  document.getElementById("bom-results").classList.remove("hidden");
}

// -- Drop Zone --

async function browseBomFile() {
  const result = await api("open_file_dialog", "Select BOM CSV", store.preferences.lastBomDir || null);
  if (!result || !result.content) return;
  if (result.directory) {
    store.preferences.lastBomDir = result.directory;
  }
  if (result.path) {
    store.preferences.lastBomFile = result.path;
  }
  savePreferences();
  publishPreferences();
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
  state.bomHeaders = headers;
  state.bomCols = cols;
  state.bomRawRows = rawRows.map(r => r.slice()); // shallow copy each row

  setBomMeta({ headers, cols });
  store.links.loadFromSaved(savedLinks);

  // Log warnings
  warnings.forEach(w => {
    AppLog.warn("BOM row " + (w.ri + 1) + ": " + w.msg);
  });

  // Match
  const { results, footprintNearMisses } = runMatch(aggregated);
  state.lastResults = results;
  state.lastFileName = fileName;
  setBomResults(results);
  setBomFootprintNearMisses(footprintNearMisses);
  setBomMeta({ fileName });

  // Log summary
  const matched = results.filter(r => r.inv).length;
  const missing = results.filter(r => !r.inv).length;
  AppLog.info("BOM loaded: " + fileName + " \u2014 " + rawRows.length + " rows, " + aggregated.size + " unique, " + matched + " matched, " + missing + " missing");

  emitBomData();
  kickSpecLookup();

  const zone = document.getElementById("bom-drop-zone");
  zone.innerHTML = renderLoadedDropZone(fileName);
  zone.classList.add("loaded");
  resetDropZoneInput("bom-file-input", handleFile);

  document.getElementById("bom-save-btn").disabled = false;
  document.getElementById("bom-consume-btn").disabled = false;
  document.getElementById("bom-clear-btn").disabled = false;
  document.getElementById("bom-add-to-cart").disabled = false;
}

// -- Manual Linking --

function createManualLink(bomRow) {
  const bk = bomKey(bomRow.bom);
  const ipk = invPartKey(store.links.linkingInvItem);
  if (!bk || !ipk) {
    showToast("Cannot create link \u2014 missing part key");
    return;
  }
  UndoRedo.save("links", snapshotLinks());
  store.links.addManualLink(bk, ipk);
  AppLog.info("Manual link: " + ipk + " \u2192 " + bk);
  store.links.setLinkingMode(false);
  showToast("Linked " + ipk + " \u2192 " + bk);
}

// -- Consume --

function resetConsumeConfirm() {
  state.consumeArmed = false;
  const btn = document.getElementById("consume-confirm");
  if (btn) {
    btn.textContent = "Consume";
    btn.classList.remove("btn-danger");
    btn.classList.add("btn-apply");
  }
}

function openConsumeModal() {
  AppLog.info("Opening consume modal");
  const mult = getMultiplier();
  const { matches: matched } = prepareConsumption(computeRows(state.lastResults, mult, store.links));
  document.getElementById("consume-subtitle").textContent =
    `Consume ${matched.length} matched parts x${mult} from "${state.lastFileName}"?`;
  document.getElementById("consume-note").value = "";
  resetConsumeConfirm();
  state.consumeModal.open();
}

// -- Init --

export function init() {
  state.body = document.getElementById("bom-body");
  state.body.innerHTML = renderDropZone();
  setupDropZone("bom-drop-zone", "bom-file-input", browseBomFile, handleFile);

  state.consumeModal = Modal("consume-modal", {
    onClose: () => resetConsumeConfirm(),
    cancelId: "consume-cancel",
    confirmId: "consume-confirm",
  });

  setupEvents({
    reprocessAndRender, emitBomData, handleFile,
    loadBomText, createManualLink, openConsumeModal, resetConsumeConfirm,
    getMultiplier, renderBomPanel, updateSaveBtnState,
  });
}
