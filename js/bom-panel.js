/* bom-panel.js — Right panel: BOM file viewer, matching, consume.
   Shows editable raw BOM rows with undo/redo, row classification, and console logging. */

(function () {
  const body = document.getElementById("bom-body");
  let lastResults = null;
  let lastFileName = "";

  // Linking mode state
  let linkingMode = false;
  let linkingInvItem = null;

  // Editable raw-row state
  let bomRawRows = [];
  let bomHeaders = [];
  let bomCols = {};
  let bomDirty = false;

  // Linkify URLs in escaped HTML text
  function linkifyHtml(escaped) {
    return escaped.replace(/https?:\/\/[^\s<&]+/g, function (url) {
      return '<a href="' + url + '" target="_blank" title="' + url + '">' + url + '</a>';
    });
  }

  function updateSaveBtnState() {
    const btn = document.getElementById("bom-save-btn");
    if (btn) btn.classList.toggle("dirty", bomDirty);
  }

  // ── Row classification ──

  function classifyBomRow(row) {
    const joined = row.join("").toLowerCase();
    if (joined.includes("subtotal") || joined.includes("total:")) return "subtotal";

    const { lcsc, mpn } = extractPartIds(row, bomCols);

    if (!lcsc && !mpn) return "warn";

    if (bomCols.qty !== -1) {
      const rawQty = parseInt(row[bomCols.qty], 10);
      if (isNaN(rawQty) || rawQty <= 0) {
        // Only warn if there's actual content in the qty cell
        if ((row[bomCols.qty] || "").trim() !== "") return "warn";
      }
    }

    return "ok";
  }

  function countBomWarnings() {
    let warns = 0;
    bomRawRows.forEach(row => {
      const cls = classifyBomRow(row);
      if (cls === "warn" || cls === "subtotal") warns++;
    });
    return warns;
  }

  // ── Aggregate from in-memory raw rows ──

  function aggregateFromRawRows() {
    return aggregateBomRows(bomRawRows, bomHeaders, bomCols).aggregated;
  }

  // ── Single source of truth: re-derive everything from raw rows ──

  function reprocessAndRender() {
    const aggregated = aggregateFromRawRows();
    const results = matchBOM(aggregated, App.inventory, App.manualLinks, App.confirmedMatches);
    lastResults = results;
    App.bomResults = results;
    App.bomHeaders = bomHeaders;
    App.bomCols = bomCols;
    emitBomData();
  }

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
          <button class="save-bom-btn" id="bom-save-btn" disabled>Save BOM</button>
          <button class="consume-btn" id="bom-consume-btn" disabled>Consume from inventory</button>
          <button class="clear-bom-btn" id="bom-clear-btn" disabled>Clear BOM</button>
          <span class="bom-price-info" id="bom-price-info"></span>
        </div>
        <div class="bom-staging-toolbar" id="bom-staging-toolbar">
          <h3 id="bom-staging-title">Staging</h3>
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
    setupSaveBom();
    setupConsume();
    setupClearBom();

    UndoRedo.register("bom", (action, data) => {
      if (action === "snapshot") return JSON.parse(JSON.stringify(bomRawRows));
      bomRawRows = data;
      reprocessAndRender();
    });
  }

  // ── Drop Zone ──

  function setupDropZone() {
    const zone = document.getElementById("bom-drop-zone");
    const fileInput = document.getElementById("bom-file-input");

    zone.addEventListener("click", (e) => {
      if (e.target.tagName !== 'INPUT') browseBomFile();
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
      const result = await api("open_file_dialog", "Select BOM CSV", App.preferences.lastBomDir || null);
      if (result && result.content) {
        if (result.directory) {
          App.preferences.lastBomDir = result.directory;
          savePreferences();
        }
        loadBomText(result.content, result.name, result.links || null);
      }
    } catch (e) { showToast("Could not open file dialog"); }
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
      showToast("Could not parse BOM — too few lines");
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
    if (Array.isArray(savedLinks)) {
      App.manualLinks = savedLinks;
      App.confirmedMatches = [];
    } else if (savedLinks && typeof savedLinks === "object") {
      App.manualLinks = Array.isArray(savedLinks.manualLinks) ? savedLinks.manualLinks : [];
      App.confirmedMatches = Array.isArray(savedLinks.confirmedMatches) ? savedLinks.confirmedMatches : [];
    } else {
      App.manualLinks = [];
      App.confirmedMatches = [];
    }

    // Log warnings
    warnings.forEach(w => {
      AppLog.warn("BOM row " + (w.ri + 1) + ": " + w.msg);
    });

    // Match
    const results = matchBOM(aggregated, App.inventory, App.manualLinks, App.confirmedMatches);
    lastResults = results;
    lastFileName = fileName;
    App.bomResults = results;
    App.bomFileName = fileName;

    // Log summary
    const matched = results.filter(r => r.inv).length;
    const missing = results.filter(r => !r.inv).length;
    AppLog.info("BOM loaded: " + fileName + " — " + rawRows.length + " rows, " + aggregated.size + " unique, " + matched + " matched, " + missing + " missing");

    emitBomData();

    const zone = document.getElementById("bom-drop-zone");
    zone.innerHTML = `<p>Loaded <strong>${escHtml(fileName)}</strong> \u2014 drop or click to replace</p>
      <input type="file" id="bom-file-input" accept=".csv,.tsv,.txt" style="display:none">`;
    zone.classList.add("loaded");
    const newInput = document.getElementById("bom-file-input");
    if (newInput) newInput.addEventListener("change", () => { if (newInput.files.length) handleFile(newInput.files[0]); });

    document.getElementById("bom-save-btn").disabled = false;
    document.getElementById("bom-consume-btn").disabled = false;
    document.getElementById("bom-clear-btn").disabled = false;
  }

  // ── Multiplier ──

  function setupMultiplier() {
    body.addEventListener("input", (e) => {
      if (e.target.id === "bom-qty-mult") {
        AppLog.info("BOM multiplier set to " + (parseInt(e.target.value, 10) || 1));
        emitBomData();
      }
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
      if (r.bom.dnp) {
        status = "dnp";
      } else if (!r.inv) {
        status = "missing";
      } else if (r.matchType === "value" || r.matchType === "fuzzy") {
        status = "possible";
      } else if (r.matchType === "manual") {
        status = r.bom.qty * mult > r.inv.qty ? "manual-short" : "manual";
      } else if (r.matchType === "confirmed") {
        status = r.bom.qty * mult > r.inv.qty ? "confirmed-short" : "confirmed";
      } else if (r.bom.qty * mult <= r.inv.qty) {
        status = "ok";
      } else {
        status = "short";
      }
      const altQty = (r.alts || []).reduce((sum, a) => sum + a.qty, 0);
      const combinedQty = (r.inv ? r.inv.qty : 0) + altQty;
      const isShort = status === "short" || status === "manual-short" || status === "confirmed-short";
      const coveredByAlts = (isShort && combinedQty >= r.bom.qty * mult);
      return { ...r, effectiveStatus: status, effectiveQty: r.bom.qty * mult, altQty, combinedQty, coveredByAlts };
    });
  }

  function emitBomData() {
    const rows = computeRows();
    if (!rows) return;
    renderBomPanel(rows);
    EventBus.emit("bom-loaded", { rows, fileName: lastFileName, multiplier: getMultiplier() });
  }

  // ── Render the BOM panel (editable raw rows + summary) ──

  function renderBomPanel(rows) {
    const mult = getMultiplier();
    const countOk = rows.filter(r => r.effectiveStatus === "ok").length;
    const countShort = rows.filter(r => r.effectiveStatus === "short" || r.effectiveStatus === "manual-short" || r.effectiveStatus === "confirmed-short").length;
    const countPossible = rows.filter(r => r.effectiveStatus === "possible").length;
    const countMissing = rows.filter(r => r.effectiveStatus === "missing").length;
    const countManual = rows.filter(r => r.effectiveStatus === "manual" || r.effectiveStatus === "manual-short").length;
    const countConfirmed = rows.filter(r => r.effectiveStatus === "confirmed" || r.effectiveStatus === "confirmed-short").length;
    const countCovered = rows.filter(r => r.coveredByAlts).length;
    const countDnp = rows.filter(r => r.effectiveStatus === "dnp").length;
    const total = rows.length;

    // Summary
    const summary = document.getElementById("bom-summary");
    const multLabel = mult > 1 ? ` (x${mult})` : "";
    summary.innerHTML = `
      <span class="bom-name">${escHtml(lastFileName)}${multLabel}</span>
      <span class="chip blue">${total} unique</span>
      ${countManual > 0 ? `<span class="chip pink">${countManual} manual</span>` : ''}
      ${countConfirmed > 0 ? `<span class="chip teal">${countConfirmed} confirmed</span>` : ''}
      <span class="chip green">${countOk} ok</span>
      <span class="chip yellow">${countShort} short</span>
      <span class="chip orange">${countPossible} possible</span>
      <span class="chip red">${countMissing} missing</span>
      ${countDnp > 0 ? `<span class="chip grey">${countDnp} DNP</span>` : ''}
      ${countCovered > 0 ? `<span class="chip green">${countCovered} covered</span>` : ''}
    `;

    // BOM price info
    const pricePerBoard = rows.reduce((sum, r) => {
      if (r.inv && r.inv.unit_price > 0) return sum + r.bom.qty * r.inv.unit_price;
      return sum;
    }, 0);
    const totalPrice = pricePerBoard * mult;
    const priceInfo = document.getElementById("bom-price-info");
    if (priceInfo) {
      const parts = [];
      if (pricePerBoard > 0) parts.push("$" + pricePerBoard.toFixed(2) + "/board");
      if (mult > 1 && totalPrice > 0) parts.push("$" + totalPrice.toFixed(2) + " total");
      priceInfo.textContent = parts.join(" \u00b7 ");
    }

    // Linking mode banner
    const bannerEl = document.getElementById("linking-banner");
    if (bannerEl) bannerEl.remove();
    if (linkingMode && linkingInvItem) {
      const banner = document.createElement("div");
      banner.className = "linking-banner";
      banner.id = "linking-banner";
      const partId = linkingInvItem.lcsc || linkingInvItem.mpn || linkingInvItem.description || "part";
      banner.innerHTML = `<span>Linking: <strong>${escHtml(partId)}</strong> \u2014 click a missing, possible, or short BOM row</span><button class="cancel-link-btn">Cancel</button>`;
      banner.querySelector(".cancel-link-btn").addEventListener("click", () => {
        EventBus.emit("linking-mode", { active: false });
      });
      const resultsEl = document.getElementById("bom-results");
      const tableWrap = resultsEl.querySelector(".bom-table-wrap");
      if (tableWrap) resultsEl.insertBefore(banner, tableWrap);
    }

    // Staging toolbar title
    const warnCount = countBomWarnings();
    const stagingTitle = document.getElementById("bom-staging-title");
    if (stagingTitle) {
      stagingTitle.textContent = "Staging (" + bomRawRows.length + " rows" + (warnCount > 0 ? ", " + warnCount + " warnings" : "") + ")";
    }

    // Build missing-key set for linking mode (DNP-aware)
    const missingKeys = new Set();
    if (linkingMode && linkingInvItem) {
      rows.forEach(r => {
        if (r.effectiveStatus === "missing" || r.effectiveStatus === "possible" || r.effectiveStatus === "short" || r.effectiveStatus === "manual-short" || r.effectiveStatus === "confirmed-short") {
          const bsk = bomAggKey(r.bom);
          if (bsk) missingKeys.add(bsk);
        }
      });
    }

    // Build status lookup: aggregation key → effectiveStatus (DNP-aware)
    const statusMap = {};
    rows.forEach(r => {
      const statusKey = bomAggKey(r.bom);
      if (statusKey) statusMap[statusKey] = r.effectiveStatus;
    });

    // Helper: derive aggregation key from a raw row (DNP-aware)
    function rawRowKey(row) {
      return rawRowAggKey(row, bomCols);
    }

    const statusIcons = { ok: "+", short: "~", possible: "?", missing: "\u2014", manual: "\u2726", confirmed: "\u2714", "manual-short": "\u2726~", "confirmed-short": "\u2714~", dnp: "\u2716" };
    const statusRowClass = { ok: "row-green", short: "row-yellow", possible: "row-orange", missing: "row-red", manual: "row-pink", confirmed: "row-teal", "manual-short": "row-pink-short", "confirmed-short": "row-teal-short", dnp: "row-dnp" };

    // Dynamic table header from CSV columns
    const hdrs = bomHeaders;
    let theadHtml = '<tr><th class="row-delete"></th><th style="width:24px"></th>';
    hdrs.forEach(h => { theadHtml += `<th>${escHtml(h)}</th>`; });
    theadHtml += '</tr>';
    document.getElementById("bom-thead").innerHTML = theadHtml;

    // Table body — editable raw rows
    const tbody = document.getElementById("bom-tbody");
    tbody.innerHTML = "";

    bomRawRows.forEach((row, ri) => {
      const cls = classifyBomRow(row);
      const tr = document.createElement("tr");

      // Determine match status for this row
      const rk = rawRowKey(row);
      const st = (cls === "ok" && rk) ? (statusMap[rk] || null) : null;

      if (cls === "warn") tr.className = "row-warn";
      else if (cls === "subtotal") tr.className = "row-subtotal";
      else if (st && statusRowClass[st]) tr.className = statusRowClass[st];

      // Linking mode: highlight missing rows as link targets
      if (linkingMode && linkingInvItem && cls === "ok") {
        if (rk && missingKeys.has(rk)) {
          tr.classList.add("link-target");
          tr.addEventListener("click", () => {
            const matchedResult = rows.find(r => {
              const k = bomAggKey(r.bom);
              return k === rk;
            });
            if (matchedResult) createManualLink(matchedResult);
          });
        }
      }

      // Delete button
      const delTd = document.createElement("td");
      delTd.className = "row-delete";
      delTd.textContent = "\u00d7";
      delTd.addEventListener("click", () => {
        UndoRedo.save("bom", bomRawRows);
        bomRawRows.splice(ri, 1);
        bomDirty = true;
        updateSaveBtnState();
        AppLog.info("Deleted BOM row " + (ri + 1));
        reprocessAndRender();
      });
      tr.appendChild(delTd);

      // Status icon cell
      const stTd = document.createElement("td");
      stTd.className = "status";
      stTd.textContent = st ? (statusIcons[st] || "") : "";
      tr.appendChild(stTd);

      // Editable cells
      hdrs.forEach((h, ci) => {
        const td = document.createElement("td");
        const inp = document.createElement("input");
        inp.type = "text";
        inp.value = (row[ci] != null) ? row[ci] : "";
        inp.addEventListener("change", () => {
          UndoRedo.save("bom", bomRawRows);
          bomRawRows[ri][ci] = inp.value;
          bomDirty = true;
          updateSaveBtnState();
          AppLog.info("Edited BOM cell [" + (ri + 1) + ", " + ci + "]");
          reprocessAndRender();
        });
        td.appendChild(inp);
        tr.appendChild(td);
      });

      tbody.appendChild(tr);
    });

    document.getElementById("bom-results").classList.remove("hidden");
  }

  // ── Save BOM ──

  function setupSaveBom() {
    body.addEventListener("click", async (e) => {
      if (e.target.id !== "bom-save-btn") return;
      if (!bomHeaders.length || !bomRawRows.length) return;
      const csvText = generateCSV(bomHeaders, bomRawRows);
      const hasLinks = App.manualLinks.length > 0 || App.confirmedMatches.length > 0;
      const linksJson = hasLinks ? JSON.stringify({
        manualLinks: App.manualLinks,
        confirmedMatches: App.confirmedMatches,
      }) : null;
      try {
        const result = await api("save_file_dialog", csvText, lastFileName || "bom.csv", App.preferences.lastBomDir || null, linksJson);
        if (result && result.path) {
          bomDirty = false;
          updateSaveBtnState();
          showToast("Saved BOM to " + result.path);
          AppLog.info("Saved BOM: " + result.path);
        }
      } catch (e) {
        showToast("Could not save BOM");
        AppLog.error("Save BOM failed: " + e.message);
      }
    });
  }

  // ── Clear BOM ──

  function setupClearBom() {
    body.addEventListener("click", (e) => {
      if (e.target.id !== "bom-clear-btn") return;
      lastResults = null;
      lastFileName = "";
      bomRawRows = [];
      bomHeaders = [];
      bomCols = {};
      bomDirty = false;
      App.bomResults = null;
      App.bomFileName = "";
      linkingMode = false;
      linkingInvItem = null;
      document.getElementById("bom-results").classList.add("hidden");
      document.getElementById("bom-thead").innerHTML = "";
      document.getElementById("bom-tbody").innerHTML = "";
      const zone = document.getElementById("bom-drop-zone");
      zone.innerHTML = `<p>Drop a BOM CSV here, or click to browse</p>
        <div class="hint">Supports JLCPCB, KiCad, and generic BOM formats</div>
        <input type="file" id="bom-file-input" accept=".csv,.tsv,.txt">`;
      zone.classList.remove("loaded");
      const newInput = document.getElementById("bom-file-input");
      if (newInput) newInput.addEventListener("change", () => { if (newInput.files.length) handleFile(newInput.files[0]); });
      AppLog.info("BOM cleared");
      EventBus.emit("bom-cleared");
    });
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

  let consumeArmed = false;

  function resetConsumeConfirm() {
    consumeArmed = false;
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
    const matched = lastResults.filter(r => r.inv && r.matchType !== "value" && r.matchType !== "fuzzy");
    const modal = document.getElementById("consume-modal");
    document.getElementById("consume-subtitle").textContent =
      `Consume ${matched.length} matched parts x${mult} from "${lastFileName}"?`;
    document.getElementById("consume-note").value = "";
    resetConsumeConfirm();
    modal.classList.remove("hidden");
  }

  document.getElementById("consume-cancel").addEventListener("click", () => {
    document.getElementById("consume-modal").classList.add("hidden");
    resetConsumeConfirm();
  });
  document.getElementById("consume-modal").addEventListener("click", (e) => {
    if (e.target.id === "consume-modal") {
      e.target.classList.add("hidden");
      resetConsumeConfirm();
    }
  });

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

    const matches = [];
    lastResults.forEach(r => {
      if (r.inv && r.matchType !== "value" && r.matchType !== "fuzzy") {
        const pk = invPartKey(r.inv);
        if (pk) matches.push({ part_key: pk, bom_qty: r.bom.qty });
      }
    });

    if (matches.length === 0) {
      showToast("No matched parts to consume");
      AppLog.warn("Consume cancelled: no matched parts");
      document.getElementById("consume-modal").classList.add("hidden");
      return;
    }

    try {
      const fresh = await api("consume_bom", JSON.stringify(matches), mult, lastFileName, note);
      if (fresh.error) {
        showToast("Error: " + fresh.error);
        AppLog.error("Consume error: " + fresh.error);
      } else {
        document.getElementById("consume-modal").classList.add("hidden");
        onInventoryUpdated(fresh);
        showToast(`Consumed ${matches.length} parts x${mult}`);
        AppLog.info("Consumed " + matches.length + " parts x" + mult + " from " + lastFileName);
      }
    } catch (e) {
      showToast("Error: " + e.message);
      AppLog.error("Consume failed: " + e.message);
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
    if (lastResults && lastFileName && bomRawRows.length) {
      reprocessAndRender();
    }
  });

  EventBus.on("confirmed-match-changed", () => {
    if (lastResults && lastFileName && bomRawRows.length) reprocessAndRender();
  });

  // ── Manual Linking ──

  function createManualLink(bomRow) {
    const bk = bomKey(bomRow.bom);
    const ipk = invPartKey(linkingInvItem);
    if (!bk || !ipk) {
      showToast("Cannot create link — missing part key");
      return;
    }
    App.manualLinks.push({ bomKey: bk, invPartKey: ipk });
    AppLog.info("Manual link: " + ipk + " → " + bk);
    EventBus.emit("linking-mode", { active: false });
    reprocessAndRender();
    showToast("Linked " + ipk + " → " + bk);
  }

  EventBus.on("linking-mode", (data) => {
    linkingMode = data.active;
    linkingInvItem = data.active ? data.invItem : null;
    // Re-render BOM panel visuals (banner/targets) without re-emitting bom-loaded
    if (lastResults) {
      const rows = computeRows();
      if (rows) renderBomPanel(rows);
    }
  });

  init();
})();
