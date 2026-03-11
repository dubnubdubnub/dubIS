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

  // BOM comparison state
  let bomData = null;        // { rows, fileName, multiplier } from bom-loaded event
  let activeFilter = "all";
  let expandedAlts = new Set();

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

  function renderBomTableRow(tbody, r, query) {
    const st = r.effectiveStatus;
    if (activeFilter !== "all" && st !== activeFilter) {
      const matchesFilter =
        (activeFilter === "manual" && st === "manual-short") ||
        (activeFilter === "confirmed" && st === "confirmed-short") ||
        (activeFilter === "short" && (st === "manual-short" || st === "confirmed-short"));
      if (!matchesFilter) return;
    }

    if (query) {
      const text = [
        r.bom.lcsc, r.bom.mpn, r.bom.value, r.bom.refs, r.bom.desc,
        r.inv ? r.inv.lcsc : "", r.inv ? r.inv.mpn : "", r.inv ? r.inv.description : "",
      ].join(" ").toLowerCase();
      if (!text.includes(query)) return;
    }

    const partKey = bomKey(r.bom);
    const tr = document.createElement("tr");
    tr.dataset.partKey = partKey;

    const rowClass = (st === "short" && r.coveredByAlts) ? "row-yellow-covered" : (STATUS_ROW_CLASS[st] || "row-red");
    tr.className = rowClass;

    const icon = (st === "short" && r.coveredByAlts) ? "~+" : (STATUS_ICONS[st] || "\u2014");
    const dispLcsc = (r.inv ? r.inv.lcsc : "") || r.bom.lcsc || "";
    const dispMpn = (r.inv ? r.inv.mpn : "") || r.bom.mpn || "";
    const invQty = r.inv ? r.inv.qty : "\u2014";
    const invDesc = r.inv ? (r.inv.description || r.inv.mpn) : (r.bom.desc || r.bom.value || "not in inventory");
    const matchLabel = r.matchType === "lcsc" ? "LCSC" : r.matchType === "mpn" ? "MPN" : r.matchType === "fuzzy" ? "Fuzzy" : r.matchType === "value" ? "Value" : r.matchType === "manual" ? "Manual" : r.matchType === "confirmed" ? "Confirmed" : "\u2014";
    const qtyClass = st === "dnp" ? "qty-dnp"
      : st === "manual" ? "qty-manual"
      : st === "manual-short" ? "qty-manual-short"
      : st === "confirmed" ? "qty-confirmed"
      : st === "confirmed-short" ? "qty-confirmed-short"
      : st === "ok" ? "qty-ok" : st === "short" ? (r.coveredByAlts ? "qty-ok" : "qty-short") : st === "possible" ? "qty-possible" : "qty-miss";

    let haveHtml = "" + invQty;
    if (r.alts && r.alts.length > 0) {
      const altS = r.alts.length === 1 ? "alt" : "alts";
      let badgeText, coveredCls = "";
      if (st === "short" || st === "manual-short" || st === "confirmed-short") {
        badgeText = r.coveredByAlts ? "\u2714 covers" : "still short";
        coveredCls = r.coveredByAlts ? " covered" : "";
      } else {
        badgeText = r.alts.length + " " + altS;
        coveredCls = " covered";
      }
      const expandedCls = expandedAlts.has(partKey) ? " expanded" : "";
      haveHtml += '<br><span class="alt-badge' + coveredCls + expandedCls + '" data-part-key="' + escHtml(partKey) + '"><span class="chevron">\u25B8</span>+' + r.altQty + ' (' + badgeText + ')</span>';
    }

    const adjBtnHtml = r.inv ? '<button class="adj-btn" title="Adjust qty">Adjust</button>' : '';
    const confirmBtnHtml = st === "possible" && r.inv
      ? '<button class="confirm-btn" title="Confirm this match">Confirm</button>'
      : (st === "confirmed" || st === "confirmed-short") && r.inv
      ? '<button class="unconfirm-btn" title="Revert to possible match">Unconfirm</button>'
      : '';
    const linkBtnHtml = r.inv ? `<button class="link-btn${App.links.linkingMode && App.links.linkingInvItem === r.inv ? ' active' : ''}" title="Link to missing BOM row">Link</button>` : '';
    const isLinkingSource = App.links.linkingMode && App.links.linkingInvItem === r.inv;
    const refsStr = r.bom.refs || "";
    tr.innerHTML = `
      <td class="refs-cell" title="${escHtml(refsStr)}">${colorizeRefs(refsStr)}</td>
      <td class="status">${icon}</td>
      <td class="mono"${dispLcsc && /^C\d{4,}$/i.test(dispLcsc) ? ' data-lcsc="' + escHtml(dispLcsc) + '"' : ''}>${escHtml(dispLcsc)}</td>
      <td class="mono" title="${escHtml(dispMpn)}">${escHtml(dispMpn)}</td>
      <td class="${qtyClass}" style="text-align:right;font-weight:600">${r.effectiveQty}</td>
      <td class="inv-qty-cell ${qtyClass}" style="text-align:right;font-weight:600">${haveHtml}</td>
      <td class="${st === 'missing' ? 'muted' : ''}">${escHtml(invDesc)}</td>
      <td class="mono" style="text-align:center">${matchLabel}</td>
      <td class="btn-group">${confirmBtnHtml}${adjBtnHtml}${linkBtnHtml}</td>
    `;
    if (isLinkingSource) tr.classList.add("linking-source");

    const confirmBtn = tr.querySelector(".confirm-btn");
    if (confirmBtn) {
      confirmBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        confirmMatch(r);
      });
    }

    const unconfirmBtn = tr.querySelector(".unconfirm-btn");
    if (unconfirmBtn) {
      unconfirmBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        unconfirmMatch(r);
      });
    }

    if (r.inv) {
      const adjTd = tr.querySelector(".adj-btn");
      if (adjTd) {
        adjTd.addEventListener("click", (e) => {
          e.stopPropagation();
          openAdjustModal(r.inv);
        });
      }
      const linkBtn = tr.querySelector(".link-btn");
      if (linkBtn) {
        linkBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          App.links.setLinkingMode(true, r.inv);
        });
      }
    }

    tbody.appendChild(tr);

    if (r.alts && r.alts.length > 0 && expandedAlts.has(partKey)) {
      renderAltRows(tbody, r.alts, partKey, st, r);
    }
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

  function renderBomComparison() {
    const query = (searchInput.value || "").toLowerCase();
    const rows = bomData.rows;
    const sortedRows = [...rows].sort((a, b) => BOM_STATUS_SORT_ORDER[a.effectiveStatus] - BOM_STATUS_SORT_ORDER[b.effectiveStatus]);
    const c = countStatuses(rows);

    renderFilterBar(c);

    // BOM matched section — table with full comparison
    const tableWrap = document.createElement("div");
    tableWrap.className = "table-wrap";
    const table = document.createElement("table");
    table.innerHTML = `<thead><tr>
      <th class="refs-col">Designators</th>
      <th style="width:24px"></th>
      <th style="width:90px">LCSC</th>
      <th style="width:140px">MPN</th>
      <th style="width:50px">Need</th>
      <th style="width:50px">Have</th>
      <th>Description</th>
      <th style="width:78px;text-align:center">Match</th>
      <th></th>
    </tr></thead>`;

    const tbody = document.createElement("tbody");
    sortedRows.forEach(r => renderBomTableRow(tbody, r, query));

    tbody.addEventListener("click", (e) => {
      const badge = e.target.closest(".alt-badge");
      if (!badge) return;
      e.stopPropagation();
      const pk = badge.dataset.partKey;
      if (expandedAlts.has(pk)) expandedAlts.delete(pk);
      else expandedAlts.add(pk);
      render();
    });

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

  function renderAltRows(tbody, alts, partKey, parentStatus, parentBom) {
    alts.forEach(alt => {
      const altTr = document.createElement("tr");
      altTr.className = "alt-row";
      altTr.dataset.altFor = partKey;
      altTr.innerHTML =
        '<td></td>' +
        '<td></td>' +
        '<td class="mono"' + (alt.lcsc && /^C\d{4,}$/i.test(alt.lcsc) ? ' data-lcsc="' + escHtml(alt.lcsc) + '"' : '') + '>' + escHtml(alt.lcsc || '') + '</td>' +
        '<td class="mono" title="' + escHtml(alt.mpn || '') + '">' + escHtml(alt.mpn || '') + '</td>' +
        '<td></td>' +
        '<td style="text-align:right;font-weight:600">' + alt.qty + '</td>' +
        '<td>' + escHtml(alt.description) + ' <span class="muted">' + escHtml(alt.package) + '</span></td>' +
        '<td></td>' +
        '<td class="btn-group"><button class="swap-btn" title="Use this alt as the selected part">Swap</button><button class="adj-btn" title="Adjust qty">Adjust</button></td>';
      altTr.querySelector(".adj-btn").addEventListener("click", (e) => {
        e.stopPropagation();
        openAdjustModal(alt);
      });
      altTr.querySelector(".swap-btn").addEventListener("click", (e) => {
        e.stopPropagation();
        confirmAltMatch(parentBom, alt);
      });
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

        const stockValue = item.qty * (item.unit_price || 0);
        const qtyColor = stockValueColor(stockValue, getThreshold(name));
        const showPriceWarn = item.qty > 0 && !(item.unit_price > 0);

        const isSource = App.links.linkingMode && App.links.linkingInvItem === item;
        const linkBtnStr = bomData ? `<button class="link-btn${isSource ? ' active' : ''}" title="Link to missing BOM row">Link</button>` : '';
        const valueStr = stockValue > 0 ? "$" + stockValue.toFixed(2) : "\u2014";
        row.innerHTML = `
          <span class="part-id"${displayId && /^C\d{4,}$/i.test(displayId) ? ' data-lcsc="' + escHtml(displayId) + '"' : ''}>${escHtml(displayId)}</span>
          <span class="part-mpn" title="${escHtml(displayMpn)}">${escHtml(displayMpn)}</span>
          <span class="part-value">${valueStr}</span>
          <span class="part-qty" style="color:${qtyColor}">${showPriceWarn ? '<button class="price-warn-btn" title="No price data — click to set">\u26A0</button>' : ''}${item.qty}</span>
          <span class="part-desc" title="${escHtml(displayDesc)}">${escHtml(displayDesc)}</span>
          <button class="adj-btn" title="Adjust qty">Adjust</button>
          ${linkBtnStr}
        `;
        if (isSource) row.classList.add("linking-source");
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

    // Apply qty adjustment
    const qtyResult = await api("adjust_part", type, pk, qty, note);
    if (!qtyResult) return;

    // Apply price update if changed
    if (priceChanged) {
      const up = !isNaN(newUp) ? newUp : null;
      const ep = !isNaN(newEp) ? newEp : null;
      const priceResult = await api("update_part_price", pk, up, ep);
      if (!priceResult) {
        AppLog.warn("Qty adjusted, but price update failed for " + pk);
        onInventoryUpdated(qtyResult);
        adjModal.close();
        return;
      }
      onInventoryUpdated(priceResult);
    } else {
      onInventoryUpdated(qtyResult);
    }

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
    const fresh = await api("update_part_price", pk, up, ep);
    if (!fresh) return;
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
    App.links.confirmMatch(bk, ipk);
    AppLog.info("Confirmed: " + bk + " \u2192 " + ipk);
    showToast("Confirmed " + bk);
  }

  function unconfirmMatch(bomRow) {
    const bk = bomKey(bomRow.bom);
    if (!bk) return;
    App.links.unconfirmMatch(bk);
    AppLog.info("Unconfirmed: " + bk);
    showToast("Unconfirmed " + bk);
  }

  function confirmAltMatch(bomRow, altInvItem) {
    const bk = bomKey(bomRow.bom);
    const ipk = invPartKey(altInvItem);
    if (!bk || !ipk) return;
    App.links.confirmMatch(bk, ipk);
    AppLog.info("Confirmed alt: " + bk + " \u2192 " + ipk);
    showToast("Confirmed " + bk + " \u2192 " + ipk);
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && App.links.linkingMode) {
      App.links.setLinkingMode(false);
    }
  });
})();
