/* bom-panel.js — Right panel: BOM file viewer, matching, consume.
   Shows the raw BOM contents. The comparison/diff is rendered in the inventory panel. */

(function () {
  const body = document.getElementById("bom-body");
  let lastResults = null;
  let lastFileName = "";

  function init() {
    body.innerHTML = `
      <div class="drop-zone" id="bom-drop-zone">
        <p>Drop a BOM CSV here, or click to browse</p>
        <div class="hint">Supports JLCPCB, KiCad, and generic BOM formats</div>
        <input type="file" id="bom-file-input" accept=".csv,.tsv,.txt">
      </div>
      <div id="bom-results" class="hidden">
        <div class="summary" id="bom-summary"></div>
        <div class="multiplier-bar" id="bom-multiplier-bar">
          <label for="bom-qty-mult">Board qty:</label>
          <input type="number" id="bom-qty-mult" value="1" min="1" step="1">
          <button class="consume-btn" id="bom-consume-btn" disabled>Consume from inventory</button>
        </div>
        <div class="bom-table-wrap">
          <table>
            <thead id="bom-thead"></thead>
            <tbody id="bom-tbody"></tbody>
          </table>
        </div>
      </div>
    `;
    setupDropZone();
    setupMultiplier();
    setupConsume();
  }

  // ── Drop Zone ──

  function setupDropZone() {
    const zone = document.getElementById("bom-drop-zone");
    const fileInput = document.getElementById("bom-file-input");

    zone.addEventListener("click", (e) => {
      if (e.target.tagName !== 'INPUT') {
        const fi = document.getElementById("bom-file-input");
        if (fi) fi.click();
        else browseBomFile();
      }
    });
    zone.addEventListener("dragover", (e) => { e.preventDefault(); e.stopPropagation(); zone.classList.add("dragover"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      e.stopPropagation();
      zone.classList.remove("dragover");
      if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
    });
    fileInput.addEventListener("change", () => {
      if (fileInput.files.length) handleFile(fileInput.files[0]);
    });
  }

  async function browseBomFile() {
    try {
      const result = await api("open_file_dialog", "Select BOM CSV");
      if (result && result.content) loadBomText(result.content, result.name);
    } catch (e) { showToast("Could not open file dialog"); }
  }

  function handleFile(file) {
    const reader = new FileReader();
    reader.onload = () => loadBomText(reader.result, file.name);
    reader.readAsText(file);
  }

  function loadBomText(text, fileName) {
    const aggregated = processBOM(text, fileName);
    if (!aggregated || aggregated.size === 0) {
      showToast("Could not parse BOM or no parts found");
      return;
    }
    const results = matchBOM(aggregated, App.inventory);
    lastResults = results;
    lastFileName = fileName;
    App.bomResults = results;
    App.bomFileName = fileName;
    emitBomData();

    const zone = document.getElementById("bom-drop-zone");
    zone.innerHTML = `<p>Loaded <strong>${escHtml(fileName)}</strong> \u2014 drop or click to replace</p>
      <input type="file" id="bom-file-input" accept=".csv,.tsv,.txt" style="display:none">`;
    zone.classList.add("loaded");
    const newInput = document.getElementById("bom-file-input");
    if (newInput) newInput.addEventListener("change", () => { if (newInput.files.length) handleFile(newInput.files[0]); });

    document.getElementById("bom-consume-btn").disabled = false;
  }

  // ── Multiplier ──

  function setupMultiplier() {
    body.addEventListener("input", (e) => {
      if (e.target.id === "bom-qty-mult") emitBomData();
    });
  }

  function getMultiplier() {
    const el = document.getElementById("bom-qty-mult");
    return el ? Math.max(1, parseInt(el.value, 10) || 1) : 1;
  }

  // ── Compute effective rows and emit to inventory panel ──

  function computeRows() {
    if (!lastResults) return null;
    const mult = getMultiplier();
    return lastResults.map(r => {
      let status;
      if (!r.inv) {
        status = "missing";
      } else if (r.matchType === "value" || r.matchType === "fuzzy") {
        status = "possible";
      } else if (r.bom.qty * mult <= r.inv.qty) {
        status = "ok";
      } else {
        status = "short";
      }
      const altQty = (r.alts || []).reduce((sum, a) => sum + a.qty, 0);
      const combinedQty = (r.inv ? r.inv.qty : 0) + altQty;
      const coveredByAlts = (status === "short" && combinedQty >= r.bom.qty * mult);
      return { ...r, effectiveStatus: status, effectiveQty: r.bom.qty * mult, altQty, combinedQty, coveredByAlts };
    });
  }

  function emitBomData() {
    const rows = computeRows();
    if (!rows) return;
    renderBomPanel(rows);
    EventBus.emit("bom-loaded", { rows, fileName: lastFileName, multiplier: getMultiplier() });
  }

  // ── Render the BOM panel (simple BOM list + summary) ──

  function renderBomPanel(rows) {
    const mult = getMultiplier();
    const countOk = rows.filter(r => r.effectiveStatus === "ok").length;
    const countShort = rows.filter(r => r.effectiveStatus === "short").length;
    const countPossible = rows.filter(r => r.effectiveStatus === "possible").length;
    const countMissing = rows.filter(r => r.effectiveStatus === "missing").length;
    const countCovered = rows.filter(r => r.coveredByAlts).length;
    const total = rows.length;

    // Summary
    const summary = document.getElementById("bom-summary");
    const multLabel = mult > 1 ? ` (x${mult})` : "";
    summary.innerHTML = `
      <span class="bom-name">${escHtml(lastFileName)}${multLabel}</span>
      <span class="chip blue">${total} unique</span>
      <span class="chip green">${countOk} ok</span>
      <span class="chip yellow">${countShort} short</span>
      <span class="chip orange">${countPossible} possible</span>
      <span class="chip red">${countMissing} missing</span>
      ${countCovered > 0 ? `<span class="chip green">${countCovered} covered</span>` : ''}
    `;

    // Table header
    document.getElementById("bom-thead").innerHTML = `<tr>
      <th style="width:24px"></th>
      <th style="width:90px">LCSC</th>
      <th style="width:130px">MPN</th>
      <th style="width:50px">Qty</th>
      <th>Value / Designators</th>
    </tr>`;

    // Sort: missing first, then possible, short, ok
    const order = { missing: 0, possible: 1, short: 2, ok: 3 };
    rows.sort((a, b) => order[a.effectiveStatus] - order[b.effectiveStatus]);

    // Table body — simple BOM list with status color
    const tbody = document.getElementById("bom-tbody");
    tbody.innerHTML = "";

    rows.forEach(r => {
      const st = r.effectiveStatus;
      const tr = document.createElement("tr");
      const rowClass = st === "ok" ? "row-green"
        : st === "short" ? (r.coveredByAlts ? "row-yellow-covered" : "row-yellow")
        : st === "possible" ? "row-orange" : "row-red";
      tr.className = rowClass;

      const icon = st === "ok" ? "+" : st === "short" ? (r.coveredByAlts ? "~+" : "~") : st === "possible" ? "?" : "-";
      const qtyClass = st === "ok" ? "qty-ok" : st === "short" ? (r.coveredByAlts ? "qty-ok" : "qty-short") : st === "possible" ? "qty-possible" : "qty-miss";
      const valueAndRef = [r.bom.value, r.bom.refs].filter(Boolean).join(" \u2014 ");

      tr.innerHTML = `
        <td class="status">${icon}</td>
        <td class="mono">${escHtml(r.bom.lcsc || "")}</td>
        <td class="mono" title="${escHtml(r.bom.mpn || "")}">${escHtml(r.bom.mpn || "")}</td>
        <td class="${qtyClass}" style="text-align:right;font-weight:600">${r.effectiveQty}</td>
        <td>${escHtml(valueAndRef)}</td>
      `;
      tbody.appendChild(tr);
    });

    document.getElementById("bom-results").classList.remove("hidden");
  }

  // ── Consume ──

  function setupConsume() {
    body.addEventListener("click", (e) => {
      if (e.target.id === "bom-consume-btn") {
        if (!lastResults || !lastFileName) return;
        openConsumeModal();
      }
    });
  }

  function openConsumeModal() {
    const mult = getMultiplier();
    const matched = lastResults.filter(r => r.inv && r.matchType !== "value" && r.matchType !== "fuzzy");
    const modal = document.getElementById("consume-modal");
    document.getElementById("consume-subtitle").textContent =
      `Consume ${matched.length} matched parts x${mult} from "${lastFileName}"?`;
    document.getElementById("consume-note").value = "";
    modal.classList.remove("hidden");
  }

  document.getElementById("consume-cancel").addEventListener("click", () => {
    document.getElementById("consume-modal").classList.add("hidden");
  });
  document.getElementById("consume-modal").addEventListener("click", (e) => {
    if (e.target.id === "consume-modal") e.target.classList.add("hidden");
  });

  document.getElementById("consume-confirm").addEventListener("click", async () => {
    if (!lastResults || !lastFileName) return;
    const mult = getMultiplier();
    const note = document.getElementById("consume-note").value;

    const matches = [];
    lastResults.forEach(r => {
      if (r.inv && r.matchType !== "value" && r.matchType !== "fuzzy") {
        const pk = r.inv.lcsc || r.inv.mpn;
        if (pk) matches.push({ part_key: pk, bom_qty: r.bom.qty });
      }
    });

    if (matches.length === 0) {
      showToast("No matched parts to consume");
      document.getElementById("consume-modal").classList.add("hidden");
      return;
    }

    try {
      const fresh = await api("consume_bom", JSON.stringify(matches), mult, lastFileName, note);
      if (fresh.error) {
        showToast("Error: " + fresh.error);
      } else {
        document.getElementById("consume-modal").classList.add("hidden");
        onInventoryUpdated(fresh);
        showToast(`Consumed ${matches.length} parts x${mult}`);
      }
    } catch (e) {
      showToast("Error: " + e.message);
    }
  });

  document.addEventListener("keydown", (e) => {
    const modal = document.getElementById("consume-modal");
    if (!modal.classList.contains("hidden") && e.key === "Escape") {
      modal.classList.add("hidden");
    }
  });

  // ── Re-match when inventory updates ──
  EventBus.on("inventory-updated", (inventory) => {
    if (lastResults && lastFileName) {
      const aggregated = new Map();
      lastResults.forEach(r => {
        const key = r.bom.lcsc || r.bom.mpn.toUpperCase();
        aggregated.set(key, r.bom);
      });
      const results = matchBOM(aggregated, inventory);
      lastResults = results;
      App.bomResults = results;
      emitBomData();
    }
  });

  init();
})();
