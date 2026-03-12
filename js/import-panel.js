/* import-panel.js — Left panel: purchase CSV import with editable staging table */

import { api, AppLog } from './api.js';
import { showToast, escHtml, Modal, setupDropZone, resetDropZoneInput } from './ui-helpers.js';
import { UndoRedo } from './undo-redo.js';
import { App, snapshotLinks, loadInventory, onInventoryUpdated } from './store.js';
import { parseCSV, processBOM, generateCSV } from './csv-parser.js';

const body = document.getElementById("import-body");

// Inventory field names that can be mapped to
const TARGET_FIELDS = [
  "Skip",
  "LCSC Part Number",
  "Digikey Part Number",
  "Manufacture Part Number",
  "Manufacturer",
  "Quantity",
  "Description",
  "Package",
  "Unit Price($)",
  "Ext.Price($)",
  "RoHS",
  "Customer NO.",
];

const PART_ID_FIELDS = ["LCSC Part Number", "Digikey Part Number", "Manufacture Part Number"];

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

function init() {
  body.innerHTML = `
    <div class="import-section">
      <div class="drop-zone" id="import-drop-zone">
        <p>Drop a purchase CSV here</p>
        <div class="hint">LCSC orders, cart exports, packing lists, DigiKey</div>
        <input type="file" id="import-file-input" accept=".csv,.tsv,.txt">
      </div>
      <div id="import-mapper" class="hidden"></div>
    </div>
  `;
  setupDropZone("import-drop-zone", "import-file-input", browseImportFile, handleImportFile);
}

async function browseImportFile() {
  const result = await api("open_file_dialog", "Select Purchase CSV", App.preferences.lastImportDir || null);
  if (!result || !result.content) return;
  if (result.directory) {
    App.preferences.lastImportDir = result.directory;
    savePreferences();
  }
  loadImportText(result.content, result.name);
}

function handleImportFile(file) {
  const reader = new FileReader();
  reader.onload = () => loadImportText(reader.result, file.name);
  reader.readAsText(file);
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

  // No subtotal filtering — all rows kept for user review

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

  AppLog.info("Loaded " + parsedRows.length + " rows from " + fileName);
  renderMapper();
}

// --- Row validation ---
function classifyRow(row) {
  const joined = row.join("").toLowerCase();
  if (joined.includes("subtotal") || joined.includes("total:")) return "subtotal";

  // Check part ID: any column mapped to a part ID field has a value
  const hasPart = PART_ID_FIELDS.some(f => {
    const colIdx = Object.keys(columnMapping).find(k => columnMapping[k] === f);
    return colIdx !== undefined && (row[parseInt(colIdx)] || "").trim() !== "";
  });

  // Check quantity
  const qtyField = Object.keys(columnMapping).find(k => columnMapping[k] === "Quantity");
  let qtyOk = true;
  if (qtyField !== undefined) {
    const raw = (row[parseInt(qtyField)] || "").replace(/,/g, "").replace(/"/g, "").trim();
    const parsed = parseInt(raw, 10);
    if (isNaN(parsed) || parsed <= 0) qtyOk = false;
  }

  if (!hasPart || !qtyOk) return "warn";
  return "ok";
}

function countWarnings() {
  let warns = 0;
  parsedRows.forEach(row => {
    const cls = classifyRow(row);
    if (cls === "warn" || cls === "subtotal") warns++;
  });
  return warns;
}

// --- Apply validation classes to table rows (without full re-render) ---
function applyRowClasses() {
  const tbody = document.querySelector("#import-mapper .import-preview tbody");
  if (!tbody) return;
  const trs = tbody.querySelectorAll("tr");
  trs.forEach((tr, i) => {
    if (i >= parsedRows.length) return;
    const cls = classifyRow(parsedRows[i]);
    tr.classList.remove("row-warn", "row-subtotal");
    if (cls === "warn") tr.classList.add("row-warn");
    else if (cls === "subtotal") tr.classList.add("row-subtotal");
  });
  updateImportButton();
}

function updateImportButton() {
  const btn = document.getElementById("do-import-btn");
  if (!btn) return;
  const warns = countWarnings();
  const warnText = warns > 0 ? " (" + warns + " warnings)" : "";
  btn.textContent = "Import " + parsedRows.length + " rows" + warnText;
}

function renderMapper() {
  const mapper = document.getElementById("import-mapper");
  mapper.classList.remove("hidden");

  let html = '<h3>Column Mapping</h3><div class="col-mapper">';

  parsedHeaders.forEach((header, i) => {
    const current = columnMapping[i] || "Skip";
    const isMapped = current !== "Skip";
    html += `
      <div class="col-mapper-row">
        <span class="source-col" title="${escHtml(header)}">${escHtml(header)}</span>
        <span class="arrow">\u2192</span>
        <select class="col-map-select${isMapped ? ' mapped' : ''}" data-col="${i}">
          ${TARGET_FIELDS.map(f => `<option value="${f}"${f === current ? ' selected' : ''}>${f}</option>`).join("")}
        </select>
      </div>
    `;
  });

  html += '</div>';

  // Editable staging table — ALL rows
  if (parsedRows.length > 0) {
    html += '<div class="staging-toolbar"><h3>Staging (' + parsedRows.length + ' rows)</h3></div>'
          + '<div class="import-preview"><table><thead><tr>';
    html += '<th class="row-delete"></th>';
    parsedHeaders.forEach((h, i) => {
      html += `<th><span class="th-label">${escHtml(h)}</span></th>`;
    });
    html += '</tr></thead><tbody>';
    parsedRows.forEach((row, ri) => {
      const cls = classifyRow(row);
      const trClass = cls === "warn" ? " class=\"row-warn\"" : cls === "subtotal" ? " class=\"row-subtotal\"" : "";
      html += `<tr${trClass}>`;
      html += `<td class="row-delete" data-row="${ri}">\u00d7</td>`;
      row.forEach((cell, ci) => {
        html += `<td><input type="text" value="${escHtml(cell)}" data-row="${ri}" data-col="${ci}"></td>`;
      });
      html += '</tr>';
    });
    html += '</tbody></table></div>';
  }

  // Import / Clear buttons
  const warns = countWarnings();
  const warnText = warns > 0 ? " (" + warns + " warnings)" : "";
  html += `
    <div class="import-btn-row">
      <button class="clear-import-btn" id="clear-import-btn" title="Clear import">✕</button>
      <button class="import-btn" id="do-import-btn">
        Import ${parsedRows.length} rows${warnText}
      </button>
    </div>
  `;

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

  // Transform ALL remaining rows — no silent filtering
  const invRows = [];
  parsedRows.forEach(row => {
    const invRow = {};
    for (const [colIdx, targetField] of Object.entries(columnMapping)) {
      if (targetField === "Skip") continue;
      let val = (row[parseInt(colIdx)] || "").trim();

      // Clean up values
      if (targetField === "Quantity") {
        val = val.replace(/,/g, "").replace(/"/g, "");
        const parsed = parseInt(val, 10);
        val = isNaN(parsed) ? "0" : String(parsed);
      }
      if (targetField === "Unit Price($)" || targetField === "Ext.Price($)") {
        val = val.replace(/[$,]/g, "");
        const parsed = parseFloat(val);
        val = isNaN(parsed) ? "" : parsed.toFixed(2);
      }

      invRow[targetField] = val;
    }

    invRows.push(invRow);
  });

  if (invRows.length === 0) {
    showToast("No rows to import");
    AppLog.warn("Import cancelled: no rows");
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
    importedCount: invRows.length,
    invRows,
  });

  const fresh = await api("import_purchases", JSON.stringify(invRows));
  if (!fresh) {
    // Roll back the undo entry we just pushed
    UndoRedo.popLast();
    if (btn) btn.disabled = false;
    return;
  }
  onInventoryUpdated(fresh);
  showToast(`Imported ${invRows.length} rows from ${importFileName}`);
  AppLog.info("Imported " + invRows.length + " parts from " + importFileName);

  // Track import for redo snapshot
  lastImportMeta = {
    importedCount: invRows.length,
    invRows,
  };

  // Reset import panel
  parsedHeaders = [];
  parsedRows = [];
  columnMapping = {};
  init();
}

init();
