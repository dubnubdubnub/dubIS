/* import-panel.js — Left panel: purchase CSV import with editable staging table */

(function () {
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
    setupDropZone();

    UndoRedo.register("import", (action, data) => {
      if (action === "snapshot") return JSON.parse(JSON.stringify(parsedRows));
      parsedRows = data;
      renderMapper();
    });
  }

  function setupDropZone() {
    const zone = document.getElementById("import-drop-zone");
    const fileInput = document.getElementById("import-file-input");

    zone.addEventListener("click", (e) => {
      if (e.target.tagName !== 'INPUT') browseImportFile();
    });
    zone.addEventListener("dragover", (e) => { e.preventDefault(); e.stopPropagation(); zone.classList.add("dragover"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      e.stopPropagation();
      zone.classList.remove("dragover");
      if (e.dataTransfer.files.length) handleImportFile(e.dataTransfer.files[0]);
    });
    fileInput.addEventListener("change", () => {
      if (fileInput.files.length) handleImportFile(fileInput.files[0]);
    });
  }

  async function browseImportFile() {
    try {
      const result = await api("open_file_dialog", "Select Purchase CSV", App.preferences.lastImportDir || null);
      if (result && result.content) {
        if (result.directory) {
          App.preferences.lastImportDir = result.directory;
          savePreferences();
        }
        loadImportText(result.content, result.name);
      }
    } catch (e) { showToast("Could not open file dialog"); }
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

    // No subtotal filtering — all rows kept for user review

    // Auto-detect columns via Python API
    try {
      const detected = await api("detect_columns", JSON.stringify(parsedHeaders));
      columnMapping = {};
      for (const [idx, field] of Object.entries(detected)) {
        columnMapping[parseInt(idx)] = field;
      }
    } catch (e) {
      columnMapping = {};
    }

    const zone = document.getElementById("import-drop-zone");
    zone.innerHTML = `<p>${escHtml(fileName)}</p><div class="hint">${parsedRows.length} rows \u2014 drop or click to replace</div>
      <input type="file" id="import-file-input" accept=".csv,.tsv,.txt" style="display:none">`;
    zone.classList.add("loaded");
    const newInput = document.getElementById("import-file-input");
    if (newInput) newInput.addEventListener("change", function () { if (this.files.length) handleImportFile(this.files[0]); });

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

    // Import button — always enabled, user has full control
    const warns = countWarnings();
    const warnText = warns > 0 ? " (" + warns + " warnings)" : "";
    html += `
      <button class="import-btn" id="do-import-btn">
        Import ${parsedRows.length} rows${warnText}
      </button>
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

    try {
      const fresh = await api("import_purchases", JSON.stringify(invRows));
      if (fresh.error) {
        showToast("Error: " + fresh.error);
        AppLog.error("Import error: " + fresh.error);
        if (btn) btn.disabled = false;
      } else {
        onInventoryUpdated(fresh);
        showToast(`Imported ${invRows.length} rows from ${importFileName}`);
        AppLog.info("Imported " + invRows.length + " parts from " + importFileName);
        // Reset import panel
        parsedHeaders = [];
        parsedRows = [];
        columnMapping = {};
        init();
      }
    } catch (e) {
      showToast("Error: " + e.message);
      AppLog.error("Import failed: " + e.message);
      if (btn) btn.disabled = false;
    }
  }

  init();
})();
