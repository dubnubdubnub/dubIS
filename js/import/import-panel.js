/* import/import-panel.js — Thin wiring: DOM events, API calls, undo/redo */

import { api, AppLog } from '../api.js';
import { showToast, escHtml, setupDropZone, resetDropZoneInput } from '../ui-helpers.js';
import { UndoRedo } from '../undo-redo.js';
import { store, onInventoryUpdated, savePreferences } from '../store.js';
import { parseCSV, generateCSV } from '../csv-parser.js';
import { TARGET_FIELDS, PO_TEMPLATES, classifyRow, countWarnings, transformImportRows } from './import-logic.js';
import { renderDropZone, renderMapper as renderMapperHtml } from './import-renderer.js';

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
  zone.classList.remove("has-direct-frame");
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

export function init() {
  body.innerHTML = renderDropZone(PO_TEMPLATES);
  setupDropZone("import-drop-zone", "import-file-input", browseImportFile, handleImportFile);
  document.querySelectorAll(".new-po-btn").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (btn.dataset.template === 'direct') {
        import('./mfg-direct/mfg-direct-panel.js').then(m => {
          m.startDirectFlow(body);
        });
      } else {
        createNewPO(btn.dataset.template);
      }
    });
  });
  setupDropZoneFrame();
}

// ── Drop-zone L-shape frame (SVG path computed from button position) ─────
//
// The import drop-zone has the ★ Direct button anchored in its bottom-right
// corner. We render the dashed perimeter as an L-shape with rounded corners
// and a constant 8px margin from the button. The path is computed at runtime
// from the button's bounding rect and re-computed on resize.
const FRAME_MARGIN = 8;        // px between button and dashed perimeter
const FRAME_OUTER_R = 8;       // outer corner radius (matches drop-zone border-radius)
const FRAME_NOTCH_R = 4;       // notch convex corner radius (C, D)
let frameObserver = null;

function buildFramePath(W, H, btnLeft, btnTop) {
  const M = FRAME_MARGIN;
  const R = FRAME_OUTER_R;
  const r = FRAME_NOTCH_R;
  // Stroke is centered on the path with stroke-width 2; leave 1px so dashes
  // aren't clipped by the SVG bounds.
  const x0 = 1, y0 = 1, x1 = W - 1, y1 = H - 1;
  // Notch top/left edges sit M away from the button on those sides; clamp
  // them to leave room for the outer rounded corners (R) plus the notch's
  // own convex corner (r) so the path is well-formed even at narrow widths.
  const notchTop = Math.max(y0 + R + r + 2, btnTop - M);
  const notchLeft = Math.max(x0 + R + r + 2, btnLeft - M);
  // Convex corners use sweep=1 (clockwise traversal). The concave F corner
  // is a quarter-arc of radius M centered on the button's NW corner — the
  // perimeter wraps around the button's corner at constant distance M
  // (sweep=0, CCW around its center).
  return [
    `M ${x0 + R} ${y0}`,
    `H ${x1 - R}`,
    `A ${R} ${R} 0 0 1 ${x1} ${y0 + R}`,                 // B: TR convex
    `V ${notchTop - r}`,
    `A ${r} ${r} 0 0 1 ${x1 - r} ${notchTop}`,           // C: right→notch-top
    `H ${btnLeft}`,
    `A ${M} ${M} 0 0 0 ${notchLeft} ${btnTop}`,          // F: concave, wraps button NW
    `V ${y1 - r}`,
    `A ${r} ${r} 0 0 1 ${notchLeft - r} ${y1}`,          // D: notch-left→bottom
    `H ${x0 + R}`,
    `A ${R} ${R} 0 0 1 ${x0} ${y1 - R}`,                 // E: BL
    `V ${y0 + R}`,
    `A ${R} ${R} 0 0 1 ${x0 + R} ${y0}`,                 // A: TL
    'Z',
  ].join(' ');
}

function updateDropZoneFrame() {
  const zone = document.getElementById('import-drop-zone');
  if (!zone || !zone.classList.contains('has-direct-frame')) return;
  const svg = zone.querySelector('.drop-zone-frame');
  const path = zone.querySelector('.drop-zone-frame-path');
  const button = zone.querySelector('[data-template="direct"]');
  if (!svg || !path || !button) return;
  const zr = zone.getBoundingClientRect();
  const br = button.getBoundingClientRect();
  if (zr.width === 0 || zr.height === 0) return;
  // SVG has inset:-2 + width/height calc(100% + 4) so it covers the drop-zone's
  // BORDER box exactly (the 2px transparent border on #import-drop-zone). In
  // pixel terms the SVG's rendered size equals zr.width × zr.height. Setting
  // viewBox to those pixel dims makes user units = pixels and SVG (0,0) =
  // (zr.left, zr.top), so button position in SVG userspace is br - zr.
  const W = zr.width;
  const H = zr.height;
  const btnLeft = br.left - zr.left;
  const btnTop = br.top - zr.top;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  path.setAttribute('d', buildFramePath(W, H, btnLeft, btnTop));
}

function setupDropZoneFrame() {
  if (frameObserver) { frameObserver.disconnect(); frameObserver = null; }
  const zone = document.getElementById('import-drop-zone');
  if (!zone) return;
  updateDropZoneFrame();
  frameObserver = new ResizeObserver(updateDropZoneFrame);
  frameObserver.observe(zone);
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
  zone.classList.remove("has-direct-frame");
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
  zone.classList.remove("has-direct-frame");
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
