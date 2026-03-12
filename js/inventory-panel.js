/* inventory-panel.js — Middle panel: inventory viewer + BOM comparison overlay.
   Normal mode: parts grouped by section with search.
   BOM mode: matched parts at top with status/need/have/alts, then remaining inventory. */

(function () {
  const BOM_STATUS_SORT_ORDER = {
    missing: 0, "manual-short": 0.4, manual: 0.5,
    "confirmed-short": 0.7, confirmed: 0.75,
    possible: 1, short: 2, ok: 3, dnp: 4,
  };

  const body = document.getElementById("inventory-body");
  const searchInput = document.getElementById("inv-search");
  let collapsedSections = new Set();

  // Hide descriptions when panel is too narrow for readable text (~520px threshold)
  const DESC_HIDE_WIDTH = 520;
  new ResizeObserver(([entry]) => {
    body.classList.toggle("narrow-panel", entry.contentRect.width < DESC_HIDE_WIDTH);
  }).observe(body);

  // Undo/redo tracking for inventory mutations
  let lastAdjustMeta = null;
  let lastPriceMeta = null;

  // BOM comparison state
  let bomData = null;        // { rows, fileName, multiplier } from bom-loaded event
  let activeFilter = "all";
  let expandedAlts = new Set();
  let rowMap = new Map();    // partKey → r, rebuilt each render for delegation

  const SECTION_ORDER = App.SECTION_ORDER;

  // ── Designator color coding ──

  const REF_COLOR_MAP = {
    R: "ref-r", RM: "ref-r",
    C: "ref-c",
    Y: "ref-osc", X: "ref-osc",
    U: "ref-ic", IC: "ref-ic", Q: "ref-ic",
    L: "ref-l",
    D: "ref-d", LED: "ref-d",
  };

  function refColorClass(ref) {
    const m = ref.trim().match(/^([A-Za-z]+)/);
    if (!m) return "";
    return REF_COLOR_MAP[m[1].toUpperCase()] || "";
  }

  function colorizeRefs(refsStr) {
    if (!refsStr) return "";
    return refsStr.split(/,\s*/).map(ref => {
      const cls = refColorClass(ref);
      return cls
        ? '<span class="' + cls + '">' + escHtml(ref) + '</span>'
        : escHtml(ref);
    }).join(", ");
  }

  // ── Reverse link helper (BOM missing row → inventory part) ──

  function createReverseLink(invItem) {
    const bomRow = App.links.linkingBomRow;
    if (!bomRow) return;
    const bk = bomKey(bomRow.bom);
    const ipk = invPartKey(invItem);
    if (!bk || !ipk) {
      showToast("Cannot create link \u2014 missing part key");
      return;
    }
    UndoRedo.save("links", snapshotLinks());
    App.links.addManualLink(bk, ipk);
    AppLog.info("Manual link: " + ipk + " \u2192 " + bk);
    App.links.setReverseLinkingMode(false);
    showToast("Linked " + ipk + " \u2192 " + bk);
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

  function renderFilterBar(c) {
    const filterBar = document.createElement("div");
    filterBar.className = "filter-bar";
    filterBar.innerHTML = `
      <button class="filter-btn${activeFilter === "all" ? " active" : ""}" data-filter="all">All (${c.total})</button>
      ${c.manual > 0 ? `<button class="filter-btn${activeFilter === "manual" ? " active" : ""}" data-filter="manual">Manual (${c.manual})</button>` : ''}
      ${c.confirmed > 0 ? `<button class="filter-btn${activeFilter === "confirmed" ? " active" : ""}" data-filter="confirmed">Confirmed (${c.confirmed})</button>` : ''}
      <button class="filter-btn${activeFilter === "ok" ? " active" : ""}" data-filter="ok">In Stock (${c.ok})</button>
      <button class="filter-btn${activeFilter === "short" ? " active" : ""}" data-filter="short">Short (${c.short})</button>
      <button class="filter-btn${activeFilter === "possible" ? " active" : ""}" data-filter="possible">Possible (${c.possible})</button>
      <button class="filter-btn${activeFilter === "missing" ? " active" : ""}" data-filter="missing">Missing (${c.missing})</button>
      ${c.dnp > 0 ? `<button class="filter-btn${activeFilter === "dnp" ? " active" : ""}" data-filter="dnp">DNP (${c.dnp})</button>` : ''}
    `;
    filterBar.querySelectorAll(".filter-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        activeFilter = btn.dataset.filter;
        render();
      });
    });
    body.appendChild(filterBar);
  }

  // ── BOM row element builder (consumes bomRowDisplayData output) ──

  function createBomRowElement(d) {
    const tr = document.createElement("tr");
    tr.dataset.partKey = d.partKey;
    tr.className = d.rowClass;
    if (d.isLinkingSource || d.isReverseLinkingSource) tr.classList.add("linking-source");
    if (d.isReverseTarget) tr.classList.add("link-target");

    let haveHtml = "" + d.invQty;
    if (d.altBadge) {
      const coveredCls = d.altBadge.covered ? " covered" : "";
      const expandedCls = d.altBadge.expanded ? " expanded" : "";
      haveHtml += '<br><span class="alt-badge' + coveredCls + expandedCls + '" data-part-key="' + escHtml(d.partKey) + '"><span class="chevron">\u25B8</span>+' + d.altBadge.altQty + ' (' + d.altBadge.badgeText + ')</span>';
    }

    const adjBtnHtml = d.showAdjust ? '<button class="adj-btn" title="Adjust qty">Adjust</button>' : '';
    const confirmBtnHtml = d.showConfirm
      ? '<button class="confirm-btn" title="Confirm this match">Confirm</button>'
      : d.showUnconfirm
        ? '<button class="unconfirm-btn" title="Revert to possible match">Unconfirm</button>'
        : '';
    const linkBtnHtml = d.showLink
      ? `<button class="link-btn${d.linkActive ? ' active' : ''}" title="${d.hasInv ? 'Link to missing BOM row' : 'Link to inventory part'}">Link</button>`
      : '';

    tr.innerHTML = `
      <td class="refs-cell" title="${escHtml(d.refs)}">${colorizeRefs(d.refs)}</td>
      <td class="status">${d.icon}</td>
      <td class="mono">${d.dispLcsc ? '<span' + (/^C\d{4,}$/i.test(d.dispLcsc) ? ' data-lcsc="' + escHtml(d.dispLcsc) + '"' : '') + '><img class="vendor-icon" src="data/lcsc-icon.ico">' + escHtml(d.dispLcsc) + '</span>' : ''}${d.dispLcsc && d.dispDigikey ? '<br>' : ''}${d.dispDigikey ? '<span data-digikey="' + escHtml(d.dispDigikey) + '" style="color:#cc6600"><img class="vendor-icon" src="data/digikey-icon.png">' + escHtml(d.dispDigikey) + '</span>' : ''}</td>
      <td class="mono" title="${escHtml(d.dispMpn)}">${escHtml(d.dispMpn)}</td>
      <td class="${d.qtyClass}" style="text-align:right;font-weight:600">${d.effectiveQty}</td>
      <td class="inv-qty-cell ${d.qtyClass}" style="text-align:right;font-weight:600">${haveHtml}</td>
      <td class="${d.isMissing ? 'muted' : ''}">${escHtml(d.invDesc)}</td>
      <td class="mono" style="text-align:center">${d.matchLabel}</td>
      <td class="btn-group">${confirmBtnHtml}${adjBtnHtml}${linkBtnHtml}</td>
    `;

    return tr;
  }

  // ── Alt rows builder ──

  function renderAltRows(tbody, alts, partKey) {
    alts.forEach(alt => {
      const altTr = document.createElement("tr");
      altTr.className = "alt-row";
      altTr.dataset.altFor = partKey;
      altTr.dataset.invKey = invPartKey(alt);
      var altLcsc = alt.lcsc || '';
      var altDigikey = alt.digikey || '';
      var altPartHtml = '';
      if (altLcsc) altPartHtml += '<span' + (/^C\d{4,}$/i.test(altLcsc) ? ' data-lcsc="' + escHtml(altLcsc) + '"' : '') + '><img class="vendor-icon" src="data/lcsc-icon.ico">' + escHtml(altLcsc) + '</span>';
      if (altLcsc && altDigikey) altPartHtml += '<br>';
      if (altDigikey) altPartHtml += '<span data-digikey="' + escHtml(altDigikey) + '" style="color:#cc6600"><img class="vendor-icon" src="data/digikey-icon.png">' + escHtml(altDigikey) + '</span>';
      altTr.innerHTML =
        '<td></td>' +
        '<td></td>' +
        '<td class="mono">' + altPartHtml + '</td>' +
        '<td class="mono" title="' + escHtml(alt.mpn || '') + '">' + escHtml(alt.mpn || '') + '</td>' +
        '<td></td>' +
        '<td style="text-align:right;font-weight:600">' + alt.qty + '</td>' +
        '<td>' + escHtml(alt.description) + ' <span class="muted">' + escHtml(alt.package) + '</span></td>' +
        '<td></td>' +
        '<td class="btn-group"><button class="swap-btn" title="Use this alt as the selected part">Swap</button><button class="adj-btn" title="Adjust qty">Adjust</button></td>';
      tbody.appendChild(altTr);
    });
  }

  // ── Delegated tbody click handler ──

  function handleBomTableClick(e) {
    // Alt badge toggle
    const badge = e.target.closest(".alt-badge");
    if (badge) {
      e.stopPropagation();
      const pk = badge.dataset.partKey;
      if (expandedAlts.has(pk)) expandedAlts.delete(pk);
      else expandedAlts.add(pk);
      render();
      return;
    }

    // Button clicks (both main rows and alt rows)
    const btn = e.target.closest("button");
    if (btn) {
      e.stopPropagation();
      const tr = btn.closest("tr");

      // Alt row buttons
      if (tr.classList.contains("alt-row")) {
        const parentKey = tr.dataset.altFor;
        const parentRow = rowMap.get(parentKey);
        const altKey = tr.dataset.invKey;
        const alt = parentRow && parentRow.alts
          ? parentRow.alts.find(a => invPartKey(a) === altKey)
          : null;
        if (!alt) return;
        if (btn.classList.contains("adj-btn")) openAdjustModal(alt);
        else if (btn.classList.contains("swap-btn")) confirmAltMatch(parentRow, alt);
        return;
      }

      // Main row buttons
      const pk = tr.dataset.partKey;
      const r = rowMap.get(pk);
      if (!r) return;
      if (btn.classList.contains("confirm-btn")) confirmMatch(r);
      else if (btn.classList.contains("unconfirm-btn")) unconfirmMatch(r);
      else if (btn.classList.contains("adj-btn")) openAdjustModal(r.inv);
      else if (btn.classList.contains("link-btn")) {
        if (r.inv) App.links.setLinkingMode(true, r.inv);
        else if (r.effectiveStatus === "missing") App.links.setReverseLinkingMode(true, r);
      }
      return;
    }

    // Reverse linking: row click on link-target
    const tr = e.target.closest("tr.link-target");
    if (tr && !tr.classList.contains("alt-row")) {
      const pk = tr.dataset.partKey;
      const r = rowMap.get(pk);
      if (r && r.inv) createReverseLink(r.inv);
    }
  }

  function renderBomComparison() {
    const query = (searchInput.value || "").toLowerCase();
    const rows = bomData.rows;
    const sortedRows = [...rows].sort((a, b) => BOM_STATUS_SORT_ORDER[a.effectiveStatus] - BOM_STATUS_SORT_ORDER[b.effectiveStatus]);
    const c = countStatuses(rows);
    const linkingState = {
      linkingMode: App.links.linkingMode,
      linkingInvItem: App.links.linkingInvItem,
      linkingBomRow: App.links.linkingBomRow,
    };

    renderFilterBar(c);

    // Build row lookup map for delegation
    rowMap = new Map();
    sortedRows.forEach(r => { rowMap.set(bomKey(r.bom), r); });

    // BOM matched section — table with full comparison
    const tableWrap = document.createElement("div");
    tableWrap.className = "table-wrap";
    const table = document.createElement("table");
    table.innerHTML = `<thead><tr>
      <th class="refs-col">Designators</th>
      <th style="width:24px"></th>
      <th style="width:110px">Part #</th>
      <th style="width:140px">MPN</th>
      <th style="width:50px">Need</th>
      <th style="width:50px">Have</th>
      <th>Description</th>
      <th style="width:78px;text-align:center">Match</th>
      <th></th>
    </tr></thead>`;

    const tbody = document.createElement("tbody");
    sortedRows.forEach(r => {
      const d = bomRowDisplayData(r, query, activeFilter, expandedAlts, linkingState);
      if (!d) return;
      tbody.appendChild(createBomRowElement(d));
      if (d.showAlts) renderAltRows(tbody, r.alts, d.partKey);
    });

    tbody.addEventListener("click", handleBomTableClick);

    table.appendChild(tbody);
    tableWrap.appendChild(table);
    body.appendChild(tableWrap);

    // Remaining inventory (not matched to BOM)
    const matchedInvKeys = new Set();
    rows.forEach(r => {
      if (r.inv) {
        const pk = invPartKey(r.inv).toUpperCase();
        if (pk) matchedInvKeys.add(pk);
      }
    });
    renderRemainingInventory(matchedInvKeys, query);
  }

  function renderRemainingInventory(matchedInvKeys, query) {
    const otherParts = {};
    App.inventory.forEach(item => {
      const pk = invPartKey(item).toUpperCase();
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

        const displayMpn = item.mpn || "";
        const displayDesc = item.description || "";

        const stockValue = item.qty * (item.unit_price || 0);
        const qtyColor = stockValueColor(stockValue, getThreshold(name));
        const showPriceWarn = item.qty > 0 && !(item.unit_price > 0);

        const isSource = App.links.linkingMode && App.links.linkingInvItem === item;
        const linkBtnStr = bomData ? `<button class="link-btn${isSource ? ' active' : ''}" title="Link to missing BOM row">Link</button>` : '';
        const valueStr = stockValue > 0 ? "$" + stockValue.toFixed(2) : "\u2014";

        let partIdsHtml = '<span class="part-ids">';
        if (item.lcsc) partIdsHtml += '<span class="part-id-lcsc" data-lcsc="' + escHtml(item.lcsc) + '"><img class="vendor-icon" src="data/lcsc-icon.ico">' + escHtml(item.lcsc) + '</span>';
        if (item.digikey) partIdsHtml += '<span class="part-id-digikey" data-digikey="' + escHtml(item.digikey) + '"><img class="vendor-icon" src="data/digikey-icon.png">' + escHtml(item.digikey) + '</span>';
        if (!item.lcsc && !item.digikey) partIdsHtml += '<span></span>';
        partIdsHtml += '</span>';

        row.innerHTML = `
          ${partIdsHtml}
          <span class="part-mpn" title="${escHtml(displayMpn)}">${escHtml(displayMpn)}</span>
          <span class="part-value">${valueStr}</span>
          <span class="part-qty" style="color:${qtyColor}">${showPriceWarn ? '<button class="price-warn-btn" title="No price data — click to set">\u26A0</button>' : ''}${item.qty}</span>
          <span class="part-desc"><span class="part-desc-inner" title="${escHtml(displayDesc)}">${escHtml(displayDesc)}</span></span>
          <button class="adj-btn" title="Adjust qty">Adjust</button>
          ${linkBtnStr}
        `;
        if (isSource) row.classList.add("linking-source");

        // Reverse linking: make inventory rows clickable targets
        if (App.links.linkingMode && App.links.linkingBomRow) {
          row.classList.add("link-target");
          row.addEventListener("click", () => createReverseLink(item));
        }

        row.querySelector(".adj-btn").addEventListener("click", (e) => {
          e.stopPropagation();
          openAdjustModal(item);
        });
        const warnBtn = row.querySelector(".price-warn-btn");
        if (warnBtn) {
          warnBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            openPriceModal(item);
          });
        }
        const linkBtnEl = row.querySelector(".link-btn");
        if (linkBtnEl) {
          linkBtnEl.addEventListener("click", (e) => {
            e.stopPropagation();
            App.links.setLinkingMode(true, item);
          });
        }
        section.appendChild(row);
      });
    }

    body.appendChild(section);
  }

  // ── Search ──
  function debounce(fn, ms) {
    let timer;
    return function(...args) {
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), ms);
    };
  }
  searchInput.addEventListener("input", debounce(() => render(), 150));

  // ── Undo/Redo handlers for inventory mutations ──

  UndoRedo.register("adjust", async (action, data) => {
    if (action === "snapshot") {
      if (lastAdjustMeta) {
        return { _undoType: "adjust-done", ...lastAdjustMeta };
      }
      return { _undoType: "adjust-none" };
    }
    if (data._undoType === "adjust") {
      const fresh = await api("remove_last_adjustments", 1);
      if (!fresh) throw new Error("Failed to undo adjustment");
      let result = fresh;
      if (data.priceChanged) {
        result = await api("update_part_price", data.partKey, data.oldUp, data.oldEp);
        if (!result) throw new Error("Failed to undo price change");
      }
      lastAdjustMeta = null;
      onInventoryUpdated(result);
      showToast("Undid adjustment for " + data.partKey);
    } else if (data._undoType === "adjust-done") {
      const qtyResult = await api("adjust_part", data.adjType, data.partKey, data.qty, data.note);
      if (!qtyResult) throw new Error("Failed to redo adjustment");
      let result = qtyResult;
      if (data.priceChanged) {
        result = await api("update_part_price", data.partKey, data.newUp, data.newEp);
        if (!result) throw new Error("Failed to redo price change");
      }
      lastAdjustMeta = { ...data };
      delete lastAdjustMeta._undoType;
      onInventoryUpdated(result);
      showToast("Redid adjustment for " + data.partKey);
    }
  });

  UndoRedo.register("price", async (action, data) => {
    if (action === "snapshot") {
      if (lastPriceMeta) {
        return { _undoType: "price-done", ...lastPriceMeta };
      }
      return { _undoType: "price-none" };
    }
    if (data._undoType === "price") {
      const fresh = await api("update_part_price", data.partKey, data.oldUp, data.oldEp);
      if (!fresh) throw new Error("Failed to undo price update");
      lastPriceMeta = null;
      onInventoryUpdated(fresh);
      showToast("Undid price update for " + data.partKey);
    } else if (data._undoType === "price-done") {
      const fresh = await api("update_part_price", data.partKey, data.newUp, data.newEp);
      if (!fresh) throw new Error("Failed to redo price update");
      lastPriceMeta = { ...data };
      delete lastPriceMeta._undoType;
      onInventoryUpdated(fresh);
      showToast("Redid price update for " + data.partKey);
    }
  });

  // ── Adjustment Modal ──
  const modalTitle = document.getElementById("modal-title");
  const modalSubtitle = document.getElementById("modal-subtitle");
  const modalQty = document.getElementById("modal-current-qty");
  const adjType = document.getElementById("adj-type");
  const adjQty = document.getElementById("adj-qty");
  const adjNote = document.getElementById("adj-note");
  const adjUnitPrice = document.getElementById("adj-unit-price");
  const adjExtPrice = document.getElementById("adj-ext-price");
  let currentPart = null;

  const adjModal = Modal("adjust-modal", {
    onClose: () => { currentPart = null; },
    cancelId: "adj-cancel",
  });
  linkPriceInputs(adjUnitPrice, adjExtPrice, () => currentPart ? currentPart.qty : 0);

  function openAdjustModal(item) {
    currentPart = item;
    const pk = invPartKey(item);
    modalTitle.textContent = pk + (item.mpn && item.lcsc ? " \u2014 " + item.mpn : "");
    modalSubtitle.textContent = item.description || item.package || "";
    modalQty.textContent = "Current qty: " + item.qty;
    adjType.value = "set";
    adjQty.value = item.qty;
    adjNote.value = "";
    adjUnitPrice.value = item.unit_price > 0 ? item.unit_price : "";
    adjExtPrice.value = item.ext_price > 0 ? item.ext_price : "";
    adjModal.open();
    adjQty.focus();
    adjQty.select();
  }

  document.getElementById("adj-apply").addEventListener("click", async () => {
    if (!currentPart) return;
    const pk = invPartKey(currentPart);
    const type = adjType.value;
    const qty = parseInt(adjQty.value, 10) || 0;
    const note = adjNote.value;

    // Check if price changed
    const newUp = parseFloat(adjUnitPrice.value);
    const newEp = parseFloat(adjExtPrice.value);
    const origUp = currentPart.unit_price || 0;
    const origEp = currentPart.ext_price || 0;
    const priceChanged = (!isNaN(newUp) && newUp !== origUp) || (!isNaN(newEp) && newEp !== origEp);

    // Save undo state
    UndoRedo.save("adjust", {
      _undoType: "adjust",
      partKey: pk,
      adjType: type,
      qty: qty,
      note: note,
      priceChanged: priceChanged,
      oldUp: origUp,
      oldEp: origEp,
      newUp: priceChanged ? (!isNaN(newUp) ? newUp : null) : null,
      newEp: priceChanged ? (!isNaN(newEp) ? newEp : null) : null,
    });

    // Apply qty adjustment
    const qtyResult = await api("adjust_part", type, pk, qty, note);
    if (!qtyResult) {
      UndoRedo.popLast();
      return;
    }

    // Apply price update if changed
    if (priceChanged) {
      const up = !isNaN(newUp) ? newUp : null;
      const ep = !isNaN(newEp) ? newEp : null;
      const priceResult = await api("update_part_price", pk, up, ep);
      if (!priceResult) {
        AppLog.warn("Qty adjusted, but price update failed for " + pk);
        UndoRedo._undo[UndoRedo._undo.length - 1].data.priceChanged = false;
        onInventoryUpdated(qtyResult);
        adjModal.close();
        return;
      }
      onInventoryUpdated(priceResult);
    } else {
      onInventoryUpdated(qtyResult);
    }

    lastAdjustMeta = {
      partKey: pk, adjType: type, qty: qty, note: note,
      priceChanged: priceChanged,
      oldUp: origUp, oldEp: origEp,
      newUp: priceChanged ? (!isNaN(newUp) ? newUp : null) : null,
      newEp: priceChanged ? (!isNaN(newEp) ? newEp : null) : null,
    };
    adjModal.close();
    showToast("Adjusted " + pk);
  });

  document.addEventListener("keydown", (e) => {
    if (adjModal.el.classList.contains("hidden")) return;
    if (e.key === "Enter" && document.activeElement !== adjNote) {
      document.getElementById("adj-apply").click();
    }
  });

  // ── Price Modal ──
  const priceTitle = document.getElementById("price-modal-title");
  const priceSubtitle = document.getElementById("price-modal-subtitle");
  const priceUnitInput = document.getElementById("price-unit");
  const priceExtInput = document.getElementById("price-ext");
  let pricePart = null;

  const priceModal = Modal("price-modal", {
    onClose: () => { pricePart = null; },
    cancelId: "price-cancel",
  });
  linkPriceInputs(priceUnitInput, priceExtInput, () => pricePart ? pricePart.qty : 0);

  function openPriceModal(item) {
    pricePart = item;
    const pk = invPartKey(item);
    priceTitle.textContent = pk + (item.mpn && item.lcsc ? " \u2014 " + item.mpn : "");
    priceSubtitle.textContent = (item.description || item.package || "") + " (qty: " + item.qty + ")";
    priceUnitInput.value = item.unit_price > 0 ? item.unit_price : "";
    priceExtInput.value = item.ext_price > 0 ? item.ext_price : "";
    priceModal.open();
    priceUnitInput.focus();
  }

  document.getElementById("price-apply").addEventListener("click", async () => {
    if (!pricePart) return;
    const pk = invPartKey(pricePart);
    const rawUp = parseFloat(priceUnitInput.value);
    const up = isNaN(rawUp) ? null : rawUp;
    const rawEp = parseFloat(priceExtInput.value);
    const ep = isNaN(rawEp) ? null : rawEp;
    if (up === null && ep === null) {
      showToast("Enter a unit or ext price");
      return;
    }

    // Save undo state
    UndoRedo.save("price", {
      _undoType: "price",
      partKey: pk,
      oldUp: pricePart.unit_price || 0,
      oldEp: pricePart.ext_price || 0,
      newUp: up,
      newEp: ep,
    });

    const fresh = await api("update_part_price", pk, up, ep);
    if (!fresh) {
      UndoRedo.popLast();
      return;
    }
    lastPriceMeta = {
      partKey: pk,
      oldUp: pricePart.unit_price || 0,
      oldEp: pricePart.ext_price || 0,
      newUp: up,
      newEp: ep,
    };
    priceModal.close();
    onInventoryUpdated(fresh);
    showToast("Price updated for " + pk);
  });

  document.addEventListener("keydown", (e) => {
    if (priceModal.el.classList.contains("hidden")) return;
    if (e.key === "Enter") document.getElementById("price-apply").click();
  });

  // ── Event listeners ──
  EventBus.on(Events.INVENTORY_LOADED, () => render());
  EventBus.on(Events.INVENTORY_UPDATED, () => render());
  EventBus.on(Events.PREFS_CHANGED, () => render());

  EventBus.on(Events.BOM_LOADED, (data) => {
    bomData = data;
    render();
  });

  EventBus.on(Events.BOM_CLEARED, () => {
    bomData = null;
    activeFilter = "all";
    expandedAlts = new Set();
    App.links.clearAll();
    render();
  });

  EventBus.on(Events.LINKING_MODE, () => render());

  // ── Confirm Match Functions ──

  function confirmMatch(bomRow) {
    const bk = bomKey(bomRow.bom);
    const ipk = invPartKey(bomRow.inv);
    if (!bk || !ipk) return;
    UndoRedo.save("links", snapshotLinks());
    App.links.confirmMatch(bk, ipk);
    AppLog.info("Confirmed: " + bk + " \u2192 " + ipk);
    showToast("Confirmed " + bk);
  }

  function unconfirmMatch(bomRow) {
    const bk = bomKey(bomRow.bom);
    if (!bk) return;
    UndoRedo.save("links", snapshotLinks());
    App.links.unconfirmMatch(bk);
    AppLog.info("Unconfirmed: " + bk);
    showToast("Unconfirmed " + bk);
  }

  function confirmAltMatch(bomRow, altInvItem) {
    const bk = bomKey(bomRow.bom);
    const ipk = invPartKey(altInvItem);
    if (!bk || !ipk) return;
    UndoRedo.save("links", snapshotLinks());
    App.links.confirmMatch(bk, ipk);
    AppLog.info("Confirmed alt: " + bk + " \u2192 " + ipk);
    showToast("Confirmed " + bk + " \u2192 " + ipk);
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && App.links.linkingMode) {
      if (App.links.linkingBomRow) App.links.setReverseLinkingMode(false);
      else App.links.setLinkingMode(false);
    }
  });
})();
