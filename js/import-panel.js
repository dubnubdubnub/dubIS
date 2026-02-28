/* import-panel.js — Left panel: purchase CSV import with column mapper */

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
  }

  function setupDropZone() {
    const zone = document.getElementById("import-drop-zone");
    const fileInput = document.getElementById("import-file-input");

    zone.addEventListener("click", (e) => {
      if (e.target.tagName !== 'INPUT') {
        const fi = document.getElementById("import-file-input");
        if (fi) fi.click();
        else browseImportFile();
      }
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
      const result = await api("open_file_dialog", "Select Purchase CSV");
      if (result && result.content) loadImportText(result.content, result.name);
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

    // Filter out summary/subtotal rows
    parsedRows = parsedRows.filter(row => {
      const joined = row.join("").toLowerCase();
      return !joined.includes("subtotal") && !joined.includes("total:");
    });

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

    renderMapper();
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

    // Preview table (first 8 rows)
    const previewRows = parsedRows.slice(0, 8);
    const mappedCols = Object.keys(columnMapping).map(Number).filter(i => columnMapping[i] !== "Skip");

    if (mappedCols.length > 0 && previewRows.length > 0) {
      html += '<h3>Preview</h3><div class="import-preview"><table><thead><tr>';
      mappedCols.forEach(i => {
        html += `<th>${escHtml(columnMapping[i])}</th>`;
      });
      html += '</tr></thead><tbody>';
      previewRows.forEach(row => {
        html += '<tr>';
        mappedCols.forEach(i => {
          html += `<td>${escHtml(row[i] || "")}</td>`;
        });
        html += '</tr>';
      });
      html += '</tbody></table></div>';
    }

    // Import button
    const hasQty = Object.values(columnMapping).includes("Quantity");
    const hasPart = Object.values(columnMapping).includes("LCSC Part Number") ||
                    Object.values(columnMapping).includes("Digikey Part Number") ||
                    Object.values(columnMapping).includes("Manufacture Part Number");
    const canImport = hasQty && hasPart;

    html += `
      <button class="import-btn" id="do-import-btn" ${canImport ? '' : 'disabled'}>
        Import ${parsedRows.length} rows
      </button>
    `;
    if (!canImport) {
      html += '<div class="import-count">Map at least one part ID column and Quantity</div>';
    }

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

    // Attach import button listener
    const importBtn = document.getElementById("do-import-btn");
    if (importBtn && canImport) {
      importBtn.addEventListener("click", doImport);
    }
  }

  async function doImport() {
    const btn = document.getElementById("do-import-btn");
    if (btn) btn.disabled = true;

    // Transform rows to inventory format
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

      // Skip rows with no part identifier or zero quantity
      const hasPart = invRow["LCSC Part Number"] || invRow["Digikey Part Number"] || invRow["Manufacture Part Number"];
      const qty = parseInt(invRow["Quantity"] || "0", 10);
      if (hasPart && qty > 0) {
        invRows.push(invRow);
      }
    });

    if (invRows.length === 0) {
      showToast("No valid rows to import");
      if (btn) btn.disabled = false;
      return;
    }

    try {
      const fresh = await api("import_purchases", JSON.stringify(invRows));
      if (fresh.error) {
        showToast("Error: " + fresh.error);
        if (btn) btn.disabled = false;
      } else {
        onInventoryUpdated(fresh);
        showToast(`Imported ${invRows.length} rows from ${importFileName}`);
        // Reset import panel
        parsedHeaders = [];
        parsedRows = [];
        columnMapping = {};
        init();
      }
    } catch (e) {
      showToast("Error: " + e.message);
      if (btn) btn.disabled = false;
    }
  }

  init();
})();
