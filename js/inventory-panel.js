/* inventory-panel.js — Middle panel: inventory viewer + BOM comparison overlay.
   Normal mode: parts grouped by section with search.
   BOM mode: matched parts at top with status/need/have/alts, then remaining inventory. */

(function () {
  const body = document.getElementById("inventory-body");
  const searchInput = document.getElementById("inv-search");
  let collapsedSections = new Set();

  // BOM comparison state
  let bomData = null;        // { rows, fileName, multiplier } from bom-loaded event
  let activeFilter = "all";
  let expandedAlts = new Set();

  const SECTION_ORDER = [
    "Connectors", "Switches", "Passives - Resistors", "Passives - Capacitors",
    "Passives - Inductors", "LEDs", "Crystals & Oscillators", "Diodes",
    "Discrete Semiconductors", "ICs - Microcontrollers",
    "ICs - Power / Voltage Regulators", "ICs - Voltage References",
    "ICs - Sensors", "ICs - Amplifiers", "ICs - Interface",
    "ICs - ESD Protection", "Mechanical & Hardware", "Other",
  ];

  function getPartKey(item) {
    return item.lcsc || item.mpn || item.digikey || "";
  }

  // ── Main render ──

  function render() {
    body.innerHTML = "";
    if (bomData) {
      renderBomComparison();
    } else {
      renderNormalInventory();
    }
  }

  // ── Normal mode: grouped by section ──

  function renderNormalInventory() {
    const query = (searchInput.value || "").toLowerCase();
    const sections = {};
    App.inventory.forEach(item => {
      const sec = item.section || "Other";
      (sections[sec] = sections[sec] || []).push(item);
    });

    SECTION_ORDER.filter(s => sections[s]).forEach(sec => {
      const filtered = filterByQuery(sections[sec], query);
      if (filtered.length > 0) renderSection(sec, filtered);
    });
  }

  // ── BOM comparison mode ──

  function renderBomComparison() {
    const query = (searchInput.value || "").toLowerCase();
    const rows = bomData.rows;

    // Sort: missing first, then possible, short, ok
    const order = { missing: 0, possible: 1, short: 2, ok: 3 };
    const sortedRows = [...rows].sort((a, b) => order[a.effectiveStatus] - order[b.effectiveStatus]);

    // Counts for filter bar
    const countOk = rows.filter(r => r.effectiveStatus === "ok").length;
    const countShort = rows.filter(r => r.effectiveStatus === "short").length;
    const countPossible = rows.filter(r => r.effectiveStatus === "possible").length;
    const countMissing = rows.filter(r => r.effectiveStatus === "missing").length;
    const total = rows.length;

    // Filter bar
    const filterBar = document.createElement("div");
    filterBar.className = "filter-bar";
    filterBar.innerHTML = `
      <button class="filter-btn${activeFilter === "all" ? " active" : ""}" data-filter="all">All (${total})</button>
      <button class="filter-btn${activeFilter === "ok" ? " active" : ""}" data-filter="ok">In Stock (${countOk})</button>
      <button class="filter-btn${activeFilter === "short" ? " active" : ""}" data-filter="short">Short (${countShort})</button>
      <button class="filter-btn${activeFilter === "possible" ? " active" : ""}" data-filter="possible">Possible (${countPossible})</button>
      <button class="filter-btn${activeFilter === "missing" ? " active" : ""}" data-filter="missing">Missing (${countMissing})</button>
    `;
    filterBar.querySelectorAll(".filter-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        activeFilter = btn.dataset.filter;
        render();
      });
    });
    body.appendChild(filterBar);

    // BOM matched section — table with full comparison
    const tableWrap = document.createElement("div");
    tableWrap.className = "table-wrap";
    const table = document.createElement("table");
    table.innerHTML = `<thead><tr>
      <th style="width:24px"></th>
      <th style="width:90px">LCSC</th>
      <th style="width:140px">MPN</th>
      <th style="width:50px">Need</th>
      <th style="width:50px">Have</th>
      <th>Description</th>
      <th style="width:55px">Match</th>
    </tr></thead>`;

    const tbody = document.createElement("tbody");

    sortedRows.forEach(r => {
      const st = r.effectiveStatus;
      if (activeFilter !== "all" && st !== activeFilter) return;

      // Query filter: search across BOM + inventory fields
      if (query) {
        const text = [
          r.bom.lcsc, r.bom.mpn, r.bom.value, r.bom.refs, r.bom.desc,
          r.inv ? r.inv.lcsc : "", r.inv ? r.inv.mpn : "", r.inv ? r.inv.description : "",
        ].join(" ").toLowerCase();
        if (!text.includes(query)) return;
      }

      const partKey = r.bom.lcsc || r.bom.mpn;
      const tr = document.createElement("tr");
      tr.dataset.partKey = partKey;

      const rowClass = st === "ok" ? "row-green"
        : st === "short" ? (r.coveredByAlts ? "row-yellow-covered" : "row-yellow")
        : st === "possible" ? "row-orange" : "row-red";
      tr.className = rowClass;

      const icon = st === "ok" ? "+" : st === "short" ? (r.coveredByAlts ? "~+" : "~") : st === "possible" ? "?" : "-";
      const dispLcsc = (r.inv ? r.inv.lcsc : "") || r.bom.lcsc || "";
      const dispMpn = (r.inv ? r.inv.mpn : "") || r.bom.mpn || "";
      const invQty = r.inv ? r.inv.qty : "\u2014";
      const invDesc = r.inv ? (r.inv.description || r.inv.mpn) : (r.bom.desc || r.bom.value || "not in inventory");
      const matchLabel = r.matchType === "lcsc" ? "LCSC" : r.matchType === "mpn" ? "MPN" : r.matchType === "fuzzy" ? "Fuzzy" : r.matchType === "value" ? "Value" : "\u2014";
      const qtyClass = st === "ok" ? "qty-ok" : st === "short" ? (r.coveredByAlts ? "qty-ok" : "qty-short") : st === "possible" ? "qty-possible" : "qty-miss";

      // Inv qty with alt badge
      let haveHtml = "" + invQty;
      if (r.alts && r.alts.length > 0) {
        const altS = r.alts.length === 1 ? "alt" : "alts";
        let badgeText, coveredCls = "";
        if (st === "short") {
          badgeText = r.coveredByAlts ? "\u2714 covers" : "still short";
          coveredCls = r.coveredByAlts ? " covered" : "";
        } else {
          badgeText = r.alts.length + " " + altS;
          coveredCls = " covered";
        }
        const expandedCls = expandedAlts.has(partKey) ? " expanded" : "";
        haveHtml += '<br><span class="alt-badge' + coveredCls + expandedCls + '" data-part-key="' + escHtml(partKey) + '"><span class="chevron">\u25B8</span>+' + r.altQty + ' (' + badgeText + ')</span>';
      }

      tr.innerHTML = `
        <td class="status">${icon}</td>
        <td class="mono">${escHtml(dispLcsc)}</td>
        <td class="mono" title="${escHtml(dispMpn)}">${escHtml(dispMpn)}</td>
        <td class="${qtyClass}" style="text-align:right;font-weight:600">${r.effectiveQty}</td>
        <td class="inv-qty-cell ${qtyClass}" style="text-align:right;font-weight:600">${haveHtml}</td>
        <td class="${st === 'missing' ? 'muted' : ''}">${escHtml(invDesc)}</td>
        <td class="mono" style="text-align:center">${matchLabel}</td>
      `;

      // Click to adjust (if matched to inventory)
      if (r.inv) {
        tr.style.cursor = "pointer";
        tr.addEventListener("click", (e) => {
          if (e.target.closest(".alt-badge")) return;
          openAdjustModal(r.inv);
        });
      }

      tbody.appendChild(tr);

      // Alt sub-rows if expanded
      if (r.alts && r.alts.length > 0 && expandedAlts.has(partKey)) {
        renderAltRows(tbody, r.alts, partKey);
      }
    });

    // Alt badge click handlers
    tbody.addEventListener("click", (e) => {
      const badge = e.target.closest(".alt-badge");
      if (!badge) return;
      e.stopPropagation();
      const pk = badge.dataset.partKey;
      if (expandedAlts.has(pk)) {
        expandedAlts.delete(pk);
      } else {
        expandedAlts.add(pk);
      }
      render();
    });

    table.appendChild(tbody);
    tableWrap.appendChild(table);
    body.appendChild(tableWrap);

    // Remaining inventory (not matched to BOM)
    const matchedInvKeys = new Set();
    rows.forEach(r => {
      if (r.inv) {
        const pk = (r.inv.lcsc || r.inv.mpn || "").toUpperCase();
        if (pk) matchedInvKeys.add(pk);
      }
    });

    const otherParts = {};
    App.inventory.forEach(item => {
      const pk = getPartKey(item).toUpperCase();
      if (matchedInvKeys.has(pk)) return;
      const sec = item.section || "Other";
      (otherParts[sec] = otherParts[sec] || []).push(item);
    });

    const otherSections = SECTION_ORDER.filter(s => otherParts[s]);
    if (otherSections.length > 0) {
      const divider = document.createElement("div");
      divider.className = "inv-section-header";
      divider.style.borderTop = "2px solid #30363d";
      divider.style.marginTop = "4px";
      divider.style.color = "#484f58";
      divider.style.cursor = "default";
      divider.textContent = "Other Inventory";
      body.appendChild(divider);

      otherSections.forEach(sec => {
        const filtered = filterByQuery(otherParts[sec], query);
        if (filtered.length > 0) renderSection(sec, filtered);
      });
    }
  }

  function renderAltRows(tbody, alts, partKey) {
    alts.forEach(alt => {
      const altTr = document.createElement("tr");
      altTr.className = "alt-row";
      altTr.dataset.altFor = partKey;
      altTr.innerHTML =
        '<td></td>' +
        '<td class="mono">' + escHtml(alt.lcsc || '') + '</td>' +
        '<td class="mono" title="' + escHtml(alt.mpn || '') + '">' + escHtml(alt.mpn || '') + '</td>' +
        '<td></td>' +
        '<td style="text-align:right;font-weight:600">' + alt.qty + '</td>' +
        '<td>' + escHtml(alt.description) + ' <span class="muted">' + escHtml(alt.package) + '</span></td>' +
        '<td></td>';
      altTr.style.cursor = "pointer";
      altTr.addEventListener("click", () => openAdjustModal(alt));
      tbody.appendChild(altTr);
    });
  }

  // ── Shared helpers ──

  function filterByQuery(parts, query) {
    if (!query) return parts;
    return parts.filter(item => {
      const text = [item.lcsc, item.mpn, item.description, item.manufacturer, item.package, item.digikey]
        .join(" ").toLowerCase();
      return text.includes(query);
    });
  }

  function renderSection(name, parts) {
    const section = document.createElement("div");
    section.className = "inv-section";

    const isCollapsed = collapsedSections.has(name);
    const header = document.createElement("div");
    header.className = "inv-section-header" + (isCollapsed ? " collapsed" : "");
    header.innerHTML = `<span class="chevron">\u25BE</span> ${escHtml(name)} <span class="inv-section-count">(${parts.length})</span>`;
    header.addEventListener("click", () => {
      if (collapsedSections.has(name)) collapsedSections.delete(name);
      else collapsedSections.add(name);
      render();
    });
    section.appendChild(header);

    if (!isCollapsed) {
      parts.forEach(item => {
        const row = document.createElement("div");
        row.className = "inv-part-row";

        const displayId = item.lcsc || item.digikey || "";
        const displayMpn = item.mpn || "";
        const displayDesc = item.description || "";

        row.innerHTML = `
          <span class="part-id">${escHtml(displayId)}</span>
          <span class="part-mpn" title="${escHtml(displayMpn)}">${escHtml(displayMpn)}</span>
          <span class="part-qty">${item.qty}</span>
          <span class="part-desc" title="${escHtml(displayDesc)}">${escHtml(displayDesc)}</span>
        `;
        row.addEventListener("click", () => openAdjustModal(item));
        section.appendChild(row);
      });
    }

    body.appendChild(section);
  }

  // ── Search ──
  searchInput.addEventListener("input", () => render());

  // ── Adjustment Modal ──
  const modal = document.getElementById("adjust-modal");
  const modalTitle = document.getElementById("modal-title");
  const modalSubtitle = document.getElementById("modal-subtitle");
  const modalQty = document.getElementById("modal-current-qty");
  const adjType = document.getElementById("adj-type");
  const adjQty = document.getElementById("adj-qty");
  const adjNote = document.getElementById("adj-note");
  let currentPart = null;

  function openAdjustModal(item) {
    currentPart = item;
    const pk = getPartKey(item);
    modalTitle.textContent = pk + (item.mpn && item.lcsc ? " \u2014 " + item.mpn : "");
    modalSubtitle.textContent = item.description || item.package || "";
    modalQty.textContent = "Current qty: " + item.qty;
    adjType.value = "set";
    adjQty.value = item.qty;
    adjNote.value = "";
    modal.classList.remove("hidden");
    adjQty.focus();
    adjQty.select();
  }

  function closeAdjustModal() {
    modal.classList.add("hidden");
    currentPart = null;
  }

  document.getElementById("adj-cancel").addEventListener("click", closeAdjustModal);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeAdjustModal();
  });

  document.getElementById("adj-apply").addEventListener("click", async () => {
    if (!currentPart) return;
    const pk = getPartKey(currentPart);
    const type = adjType.value;
    const qty = parseInt(adjQty.value, 10) || 0;
    const note = adjNote.value;

    try {
      const fresh = await api("adjust_part", type, pk, qty, note);
      if (fresh.error) {
        showToast("Error: " + fresh.error);
      } else {
        closeAdjustModal();
        onInventoryUpdated(fresh);
        showToast("Adjusted " + pk);
      }
    } catch (e) {
      showToast("Error: " + e.message);
    }
  });

  document.addEventListener("keydown", (e) => {
    if (modal.classList.contains("hidden")) return;
    if (e.key === "Escape") closeAdjustModal();
    if (e.key === "Enter" && document.activeElement !== adjNote) {
      document.getElementById("adj-apply").click();
    }
  });

  // ── Event listeners ──
  EventBus.on("inventory-loaded", () => render());
  EventBus.on("inventory-updated", () => render());

  EventBus.on("bom-loaded", (data) => {
    bomData = data;
    render();
  });

  EventBus.on("bom-cleared", () => {
    bomData = null;
    activeFilter = "all";
    expandedAlts = new Set();
    render();
  });
})();
