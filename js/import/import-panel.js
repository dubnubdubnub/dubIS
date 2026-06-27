/* import/import-panel.js — Thin wiring: DOM events, API calls, undo/redo */

import { api, AppLog, apiMfgDirect, whenPywebviewReady } from '../api.js';
import { showToast, escHtml, setupDropZone, resetDropZoneInput } from '../ui-helpers.js';
import { UndoRedo } from '../undo-redo.js';
import { store, onInventoryUpdated, savePreferences } from '../store.js';
import { parseCSV, generateCSV } from '../csv-parser.js';
import { TARGET_FIELDS, PO_TEMPLATES, classifyRow, countWarnings, transformImportRows, seedManualRows } from './import-logic.js';
import { renderDropZone, renderMapper as renderMapperHtml, renderOcrEngineNotice } from './import-renderer.js';
import { computeImportDiff } from './import-diff.js';
import { openImportDiffModal } from './import-diff-modal.js';

const body = document.getElementById("import-body");

let parsedHeaders = [];
let parsedRows = [];
let columnMapping = {}; // source index -> target field name
let importFileName = "";
let lastImportMeta = null; // set after successful import for undo

// ── Undo/Redo for import panel (registered once at module scope) ──
async function handleImportUndo(data) {
  const fresh = await api("remove_last_purchases", data.importedCount);
  if (!fresh) throw new Error("Failed to undo import");
  onInventoryUpdated(fresh);
  // Restore staging panel state
  parsedHeaders = data.parsedHeaders;
  parsedRows = data.parsedRows;
  columnMapping = data.columnMapping;
  importFileName = data.importFileName;
  lastImportMeta = null;

  const zone = document.getElementById("import-drop-zone");
  zone.innerHTML = `<p>${escHtml(importFileName)}</p><div class="hint">${parsedRows.length} rows \u2014 drop or click to replace</div>
    <input type="file" id="import-file-input" accept=".csv,.tsv,.txt" style="display:none">`;
  zone.classList.add("loaded");
  resetDropZoneInput("import-file-input", handleImportFile);
  renderMapper();
  showToast("Undid import of " + data.importedCount + " rows");
}

async function handleImportRedo(data) {
  const fresh = await api("import_purchases", JSON.stringify(data.invRows));
  if (!fresh) throw new Error("Failed to redo import");
  onInventoryUpdated(fresh);
  lastImportMeta = {
    importedCount: data.invRows.length,
    invRows: data.invRows,
  };
  parsedHeaders = [];
  parsedRows = [];
  columnMapping = {};
  importFileName = "";
  init();
  showToast("Redid import of " + data.invRows.length + " rows");
}

UndoRedo.register("import", async (action, data) => {
  if (action === "snapshot") {
    if (lastImportMeta) {
      return {
        _undoType: "import-done",
        invRows: lastImportMeta.invRows,
      };
    }
    return JSON.parse(JSON.stringify(parsedRows));
  }
  // action === "restore"
  if (data && data._undoType === "import") {
    await handleImportUndo(data);
  } else if (data && data._undoType === "import-done") {
    await handleImportRedo(data);
  } else {
    // Plain array — existing cell-edit / row-delete restore
    parsedRows = data;
    lastImportMeta = null;
    renderMapper();
  }
});

/** Current OCR template selection (defaults to 'generic'). */
function ocrTemplate() {
  const sel = document.getElementById("import-ocr-template");
  return (sel && sel.value) || "generic";
}

export function init() {
  body.innerHTML = renderDropZone(PO_TEMPLATES, store.preferences.lastOcrTemplate || "generic");

  // Persist the OCR-template choice so it survives the re-init after each
  // import (otherwise the dropdown snaps back to "generic" every time).
  const ocrSel = document.getElementById("import-ocr-template");
  if (ocrSel) {
    ocrSel.addEventListener("change", () => {
      store.preferences.lastOcrTemplate = ocrSel.value;
      savePreferences();
    });
  }

  // ── CSV / TSV / TXT / XLS zone (existing inline flow) ──
  setupDropZone("import-drop-zone", "import-file-input", browseImportFile, handleImportFile);
  document.querySelectorAll("#new-po-row .new-po-btn[data-template]").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      createNewPO(btn.dataset.template);
    });
  });

  const addRowBtn = document.getElementById("import-add-row");
  if (addRowBtn) {
    addRowBtn.addEventListener("click", (e) => { e.stopPropagation(); seedManualRow(); });
  }

  // ── Image / PDF / Phone zone (OCR overlay + phone scan) ──
  setupDropZone(
    "import-ocr-zone",
    "import-ocr-input",
    () => document.getElementById("import-ocr-input").click(),
    (files) => import('./mfg-direct/mfg-direct-panel.js').then(m => m.beginScanImport(body, files, ocrTemplate())),
    { multi: true },
  );

  const scanBtn = document.getElementById("import-scan-btn");
  if (scanBtn) {
    scanBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      import('./mfg-direct/mfg-direct-panel.js').then(m => m.startPhoneScan(body, ocrTemplate()));
    });
  }

  // Async: if the Tesseract OCR engine is missing, surface an in-app install
  // affordance in the image/PDF zone. Best-effort — failure to check is logged.
  refreshOcrEngineNotice();
}

/**
 * Check whether the OCR engine is available and, if not, render the missing-
 * engine notice (with Install button + copyable command) into the OCR zone.
 */
async function refreshOcrEngineNotice() {
  // The panel inits before the pywebview bridge is hydrated (app-init.js calls
  // initImportPanel() synchronously, ahead of its own whenPywebviewReady()).
  // Without this gate the engine check fires against the empty placeholder
  // bridge; api() swallows the resulting "not a function" error to undefined,
  // and the missing-engine notice renders on every launch even when Tesseract
  // is installed.
  await whenPywebviewReady();
  let available;
  try {
    available = await apiMfgDirect.ocrEngineAvailable();
  } catch (exc) {
    AppLog.warn('ocr_engine_available check failed: ' + exc);
    return;
  }
  // Only surface the install affordance when the engine is *definitively*
  // absent. undefined/null means the check was inconclusive (e.g. a swallowed
  // bridge error) — don't show a false "engine missing" notice.
  if (available !== false) return;

  const ocrZone = document.getElementById("import-ocr-zone");
  if (!ocrZone || document.getElementById("ocr-engine-missing")) return;
  ocrZone.insertAdjacentHTML("afterbegin", renderOcrEngineNotice());
  wireInstallTesseract();
}

/** Wire the in-zone "Install Tesseract" button to the backend install method. */
function wireInstallTesseract() {
  const btn = document.getElementById("install-tesseract-btn");
  if (!btn) return;
  btn.addEventListener("click", async (e) => {
    e.stopPropagation();
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Installing… approve the Windows prompt";
    let res;
    try {
      res = await apiMfgDirect.installTesseract();
    } catch (exc) {
      AppLog.error('install_tesseract call failed: ' + exc);
      res = undefined;
    }
    if (res && (res.ok || res.available)) {
      showToast("Tesseract installed");
      AppLog.info("Tesseract OCR engine installed via in-app button");
      const notice = document.getElementById("ocr-engine-missing");
      if (notice) notice.remove();
      return;
    }
    btn.disabled = false;
    btn.textContent = label;
    const msg = (res && res.message) || "Install failed — run: winget install UB-Mannheim.TesseractOCR";
    showToast(msg);
    AppLog.warn("Tesseract install failed: " + msg);
  });
}

/**
 * Seed an inline manual-entry session: one blank row using the generic PO
 * headers, with an identity column mapping, then render the editable staging
 * table so the user can type and import via the existing path.
 */
function seedManualRow() {
  const seed = seedManualRows(PO_TEMPLATES.generic);
  parsedHeaders = seed.parsedHeaders;
  parsedRows = seed.parsedRows;
  columnMapping = seed.columnMapping;
  importFileName = "Manual entry";
  lastImportMeta = null;

  const newPoRow = document.getElementById("new-po-row");
  if (newPoRow) newPoRow.classList.add("hidden");

  AppLog.info("Started manual entry import");
  renderMapper();
}

async function browseImportFile() {
  const result = await api("open_file_dialog", "Select Purchase CSV", store.preferences.lastImportDir || null);
  if (!result || !result.content) return;
  if (result.directory) {
    store.preferences.lastImportDir = result.directory;
    savePreferences();
  }
  loadImportText(result.content, result.name);
}

function handleImportFile(file) {
  if (/\.xlsx?$/i.test(file.name)) {
    showToast("XLS files: use the file browser (click the drop zone) instead of drag-drop");
    return;
  }
  const reader = new FileReader();
  reader.onload = () => loadImportText(reader.result, file.name);
  reader.readAsText(file);
}

async function createNewPO(templateKey = "generic") {
  const template = PO_TEMPLATES[templateKey] || PO_TEMPLATES.generic;
  const headers = template.headers;
  const csvContent = generateCSV(headers, []);

  const result = await api("save_file_dialog", csvContent, "purchase_order.csv", store.preferences.lastImportDir || null);
  if (!result || !result.path) return;

  // Save directory preference
  const dir = result.path.replace(/[\\/][^\\/]+$/, "");
  if (dir) {
    store.preferences.lastImportDir = dir;
    savePreferences();
  }

  const fileName = result.path.replace(/^.*[\\/]/, "");

  // Load template into staging
  parsedHeaders = [...headers];
  parsedRows = [headers.map(() => "")];
  importFileName = fileName;
  lastImportMeta = null;

  // Direct column mapping (headers match target fields exactly)
  columnMapping = {};
  headers.forEach((h, i) => { columnMapping[i] = h; });

  const zone = document.getElementById("import-drop-zone");
  zone.innerHTML = `<p>${escHtml(fileName)}</p><div class="hint">${parsedRows.length} rows \u2014 drop or click to replace</div>
    <input type="file" id="import-file-input" accept=".csv,.tsv,.txt" style="display:none">`;
  zone.classList.add("loaded");
  resetDropZoneInput("import-file-input", handleImportFile);

  const newPoRow = document.getElementById("new-po-row");
  if (newPoRow) newPoRow.classList.add("hidden");

  AppLog.info("Created blank PO template: " + fileName);
  renderMapper();
}

async function loadImportText(text, fileName) {
  const lines = parseCSV(text);
  if (lines.length < 2) {
    showToast("CSV has no data rows");
    return;
  }

  parsedHeaders = lines[0];
  parsedRows = lines.slice(1).filter(row => row.some(cell => cell !== ""));
  importFileName = fileName;
  lastImportMeta = null;

  // Auto-detect columns via Python API
  const detected = await api("detect_columns", JSON.stringify(parsedHeaders));
  columnMapping = {};
  if (detected) {
    for (const [idx, field] of Object.entries(detected)) {
      columnMapping[parseInt(idx)] = field;
    }
  }

  const zone = document.getElementById("import-drop-zone");
  zone.innerHTML = `<p>${escHtml(fileName)}</p><div class="hint">${parsedRows.length} rows \u2014 drop or click to replace</div>
    <input type="file" id="import-file-input" accept=".csv,.tsv,.txt" style="display:none">`;
  zone.classList.add("loaded");
  resetDropZoneInput("import-file-input", handleImportFile);

  const newPoRow = document.getElementById("new-po-row");
  if (newPoRow) newPoRow.classList.add("hidden");

  AppLog.info("Loaded " + parsedRows.length + " rows from " + fileName);
  renderMapper();
}

// --- Apply validation classes to table rows (without full re-render) ---
function applyRowClasses() {
  const tbody = document.querySelector("#import-mapper .import-preview tbody");
  if (!tbody) return;
  const trs = tbody.querySelectorAll("tr");
  trs.forEach((tr, i) => {
    if (i >= parsedRows.length) return;
    const cls = classifyRow(parsedRows[i], columnMapping);
    tr.classList.remove("row-warn", "row-subtotal");
    if (cls === "warn") tr.classList.add("row-warn");
    else if (cls === "subtotal") tr.classList.add("row-subtotal");
  });
  updateImportButton();
}

function updateImportButton() {
  const btn = document.getElementById("do-import-btn");
  if (!btn) return;
  const warns = countWarnings(parsedRows, columnMapping);
  const warnText = warns > 0 ? " (" + warns + " warnings)" : "";
  btn.textContent = "Import " + parsedRows.length + " rows" + warnText;
}

function renderMapper() {
  const mapper = document.getElementById("import-mapper");
  mapper.classList.remove("hidden");

  // Once a CSV/manual staging session is active, the image/PDF zone is no longer
  // relevant — collapse it so the staging table and Import button stay reachable
  // within the (scrollable) import panel at short viewports. init() re-renders the
  // panel fresh, restoring both zones.
  const ocrZone = document.getElementById("import-ocr-zone");
  if (ocrZone) ocrZone.classList.add("hidden");

  const html = renderMapperHtml(parsedHeaders, parsedRows, columnMapping, TARGET_FIELDS, importFileName);
  mapper.innerHTML = html;

  // Attach select change listeners
  mapper.querySelectorAll(".col-map-select").forEach(sel => {
    sel.addEventListener("change", () => {
      const colIdx = parseInt(sel.dataset.col);
      const val = sel.value;
      if (val === "Skip") {
        delete columnMapping[colIdx];
      } else {
        // Remove duplicate target assignments
        for (const [k, v] of Object.entries(columnMapping)) {
          if (v === val && parseInt(k) !== colIdx) delete columnMapping[parseInt(k)];
        }
        columnMapping[colIdx] = val;
      }
      renderMapper();
    });
  });

  // Attach cell edit listeners
  mapper.querySelectorAll(".import-preview td input").forEach(inp => {
    inp.addEventListener("change", () => {
      const ri = parseInt(inp.dataset.row);
      const ci = parseInt(inp.dataset.col);
      UndoRedo.save("import", parsedRows);
      parsedRows[ri][ci] = inp.value;
      applyRowClasses();
    });
  });

  // Attach row delete listeners
  mapper.querySelectorAll(".import-preview .row-delete").forEach(del => {
    del.addEventListener("click", () => {
      const ri = parseInt(del.dataset.row);
      if (ri >= 0 && ri < parsedRows.length) {
        UndoRedo.save("import", parsedRows);
        parsedRows.splice(ri, 1);
        AppLog.info("Deleted staging row " + (ri + 1));
        renderMapper();
      }
    });
  });

  // Attach add-row button listener
  const addRowBtn = document.getElementById("add-staging-row");
  if (addRowBtn) {
    addRowBtn.addEventListener("click", () => {
      UndoRedo.save("import", parsedRows);
      parsedRows.push(parsedHeaders.map(() => ""));
      renderMapper();
    });
  }

  // Attach import button listener
  const importBtn = document.getElementById("do-import-btn");
  if (importBtn) {
    importBtn.addEventListener("click", doImport);
  }

  // Attach clear button listener
  const clearBtn = document.getElementById("clear-import-btn");
  if (clearBtn) {
    clearBtn.addEventListener("click", clearImport);
  }
}

function clearImport() {
  parsedHeaders = [];
  parsedRows = [];
  columnMapping = {};
  importFileName = "";
  lastImportMeta = null;
  AppLog.info("Cleared import panel");
  init();
}

async function doImport() {
  const btn = document.getElementById("do-import-btn");
  if (btn) btn.disabled = true;

  // Transform ALL remaining rows
  const invRows = transformImportRows(parsedRows, columnMapping, TARGET_FIELDS);

  if (invRows.length === 0) {
    showToast("No rows to import");
    AppLog.warn("Import cancelled: no rows");
    if (btn) btn.disabled = false;
    return;
  }

  // Compute diff against current inventory and open review modal.
  // The user can include/exclude rows, then confirm or go back.
  const diffEntries = computeImportDiff(invRows, store.inventory || []);

  openImportDiffModal(
    diffEntries,
    // onConfirm: commit only the included rows via existing API path
    async (includedRows) => {
      if (includedRows.length === 0) {
        showToast("No rows selected — import cancelled");
        AppLog.info("Import review: no rows included, import skipped");
        if (btn) btn.disabled = false;
        return;
      }

      // Save undo state before mutating backend
      UndoRedo.save("import", {
        _undoType: "import",
        parsedRows: JSON.parse(JSON.stringify(parsedRows)),
        parsedHeaders: JSON.parse(JSON.stringify(parsedHeaders)),
        columnMapping: JSON.parse(JSON.stringify(columnMapping)),
        importFileName,
        importedCount: includedRows.length,
        invRows: includedRows,
      });

      const fresh = await api("import_purchases", JSON.stringify(includedRows));
      if (!fresh) {
        // Roll back the undo entry we just pushed
        UndoRedo.popLast();
        if (btn) btn.disabled = false;
        return;
      }
      onInventoryUpdated(fresh);
      showToast(`Imported ${includedRows.length} rows from ${importFileName}`);
      AppLog.info("Imported " + includedRows.length + " parts from " + importFileName);

      // Track import for redo snapshot
      lastImportMeta = {
        importedCount: includedRows.length,
        invRows: includedRows,
      };

      // Reset import panel
      parsedHeaders = [];
      parsedRows = [];
      columnMapping = {};
      init();
    },
    // onBack: return to staging with no commit
    () => {
      if (btn) btn.disabled = false;
      AppLog.info("Import review: user went back to staging");
    },
    // onCancel: dismiss with no commit
    () => {
      if (btn) btn.disabled = false;
      AppLog.info("Import review: user cancelled");
    },
  );
}
