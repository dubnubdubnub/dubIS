/* bom/bom-events.js — Event listener setup for the BOM panel.
   Extracted from init() to keep bom-panel.js focused on core logic. */

import { EventBus, Events } from '../event-bus.js';
import { api, AppLog } from '../api.js';
import { showToast, escHtml, resetDropZoneInput } from '../ui-helpers.js';
import { UndoRedo } from '../undo-redo.js';
import { store, setBomDirty, setBomResults, setBomMeta, onInventoryUpdated, savePreferences } from '../store.js';
import { bomAggKey } from '../part-keys.js';
import { generateCSV } from '../csv-parser.js';
import { computeRows, prepareConsumption } from './bom-logic.js';
import state from './bom-state.js';

/**
 * Wire up all DOM event listeners and EventBus subscriptions.
 * @param {object} handlers - core logic functions from bom-panel.js
 */
export function setupEvents(handlers) {
  const {
    reprocessAndRender, emitBomData, handleFile,
    loadBomText, createManualLink, openConsumeModal, resetConsumeConfirm,
    getMultiplier, renderBomPanel, updateSaveBtnState,
  } = handlers;

  // Multiplier input
  state.body.addEventListener("input", (e) => {
    if (e.target.id === "bom-qty-mult") {
      AppLog.info("BOM multiplier set to " + (parseInt(e.target.value, 10) || 1));
      emitBomData();
    }
  });

  // Save BOM
  state.body.addEventListener("click", async (e) => {
    if (e.target.id === "bom-save-btn") {
      if (!state.bomHeaders.length || !state.bomRawRows.length) return;
      const csvText = generateCSV(state.bomHeaders, state.bomRawRows);
      const linksJson = store.links.hasLinks() ? JSON.stringify({
        manualLinks: store.links.manualLinks,
        confirmedMatches: store.links.confirmedMatches,
      }) : null;
      const result = await api("save_file_dialog", csvText, state.lastFileName || "bom.csv", store.preferences.lastBomDir || null, linksJson);
      if (result && result.path) {
        state.bomDirty = false;
        setBomDirty(false);
        api("set_bom_dirty", false);
        updateSaveBtnState();
        store.preferences.lastBomFile = result.path;
        savePreferences();
        showToast("Saved BOM to " + result.path);
        AppLog.info("Saved BOM: " + result.path);
      }
    }
  });

  // Clear BOM
  state.body.addEventListener("click", (e) => {
    if (e.target.id === "bom-clear-btn") {
      state.lastResults = null;
      state.lastFileName = "";
      state.bomRawRows = [];
      state.bomHeaders = [];
      state.bomCols = {};
      state.bomDirty = false;
      setBomDirty(false);
      api("set_bom_dirty", false);
      setBomResults(null);
      setBomMeta({ fileName: "" });
      store.preferences.lastBomFile = null;
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
  state.body.addEventListener("click", (e) => {
    if (e.target.id === "bom-consume-btn") {
      if (!state.lastResults || !state.lastFileName) return;
      openConsumeModal();
    }
  });

  // Consume confirm
  document.getElementById("consume-confirm").addEventListener("click", async () => {
    if (!state.consumeArmed) {
      state.consumeArmed = true;
      const btn = document.getElementById("consume-confirm");
      btn.textContent = "Are you sure?";
      btn.classList.remove("btn-apply");
      btn.classList.add("btn-danger");
      return;
    }
    resetConsumeConfirm();
    if (!state.lastResults || !state.lastFileName) return;
    const mult = getMultiplier();
    const note = document.getElementById("consume-note").value;

    const { matches, matchesJson } = prepareConsumption(state.lastResults);

    if (matches.length === 0) {
      showToast("No matched parts to consume");
      AppLog.warn("Consume cancelled: no matched parts");
      state.consumeModal.close();
      return;
    }

    UndoRedo.save("consume", {
      _undoType: "consume",
      adjustmentCount: matches.length,
      matchesJson: matchesJson,
      mult: mult,
      bomName: state.lastFileName,
      note: note,
    });

    const fresh = await api("consume_bom", matchesJson, mult, state.lastFileName, note);
    if (!fresh) {
      UndoRedo.popLast();
      return;
    }
    state.lastConsumeMeta = {
      matchesJson: matchesJson,
      mult: mult,
      bomName: state.lastFileName,
      note: note,
      adjustmentCount: matches.length,
    };
    state.consumeModal.close();
    onInventoryUpdated(fresh);
    showToast(`Consumed ${matches.length} parts x${mult}`);
    AppLog.info("Consumed " + matches.length + " parts x" + mult + " from " + state.lastFileName);
  });

  // Event delegation on staging tbody for row interactions
  const tbodyEl = document.getElementById("bom-tbody");

  tbodyEl.addEventListener("click", (e) => {
    // Delete button
    const deleteTarget = e.target.closest('[data-action="delete"]');
    if (deleteTarget) {
      const ri = parseInt(deleteTarget.dataset.ri, 10);
      UndoRedo.save("bom", state.bomRawRows);
      state.bomRawRows.splice(ri, 1);
      state.bomDirty = true;
      setBomDirty(true);
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
      const rows = computeRows(state.lastResults, getMultiplier(), store.links);
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
      UndoRedo.save("bom", state.bomRawRows);
      state.bomRawRows[ri][ci] = e.target.value;
      state.bomDirty = true;
      setBomDirty(true);
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
    if (action === "snapshot") return JSON.parse(JSON.stringify(state.bomRawRows));
    state.bomRawRows = data;
    reprocessAndRender();
  });

  UndoRedo.register("consume", async (action, data) => {
    if (action === "snapshot") {
      if (state.lastConsumeMeta) {
        return { _undoType: "consume-done", ...state.lastConsumeMeta };
      }
      return { _undoType: "consume-none" };
    }
    if (data._undoType === "consume") {
      const fresh = await api("remove_last_adjustments", data.adjustmentCount);
      if (!fresh) throw new Error("Failed to undo consume");
      state.lastConsumeMeta = null;
      onInventoryUpdated(fresh);
      showToast("Undid consume of " + data.adjustmentCount + " parts");
    } else if (data._undoType === "consume-done") {
      const fresh = await api("consume_bom", data.matchesJson, data.mult, data.bomName, data.note);
      if (!fresh) throw new Error("Failed to redo consume");
      state.lastConsumeMeta = {
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
    if (state.lastResults && state.lastFileName && state.bomRawRows.length) {
      reprocessAndRender();
    }
  });

  EventBus.on(Events.CONFIRMED_CHANGED, () => {
    if (state.lastResults && state.lastFileName && state.bomRawRows.length) reprocessAndRender();
  });

  EventBus.on(Events.LINKS_CHANGED, () => {
    if (state.lastResults && state.lastFileName && state.bomRawRows.length) reprocessAndRender();
  });

  EventBus.on(Events.LINKING_MODE, () => {
    if (state.lastResults) {
      const rows = computeRows(state.lastResults, getMultiplier(), store.links);
      if (rows) renderBomPanel(rows);
    }
  });

  EventBus.on(Events.SAVE_AND_CLOSE, async () => {
    if (!state.bomHeaders.length || !state.bomRawRows.length) {
      api("confirm_close");
      return;
    }
    const csvText = generateCSV(state.bomHeaders, state.bomRawRows);
    const linksJson = store.links.hasLinks() ? JSON.stringify({
      manualLinks: store.links.manualLinks,
      confirmedMatches: store.links.confirmedMatches,
    }) : null;
    const result = await api("save_file_dialog", csvText, state.lastFileName || "bom.csv", store.preferences.lastBomDir || null, linksJson);
    if (result && result.path) {
      state.bomDirty = false;
      setBomDirty(false);
      api("set_bom_dirty", false);
      store.preferences.lastBomFile = result.path;
      await savePreferences();
    }
    api("confirm_close");
  });

  EventBus.on(Events.INVENTORY_LOADED, async () => {
    const lastFile = store.preferences.lastBomFile;
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
