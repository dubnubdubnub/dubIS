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

  // Linking mode state
  let linkingMode = false;
  let linkingInvItem = null;

  const SECTION_ORDER = App.SECTION_ORDER;

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

    // Sort: manual first, then missing, confirmed, possible, short, ok
    const sortedRows = [...rows].sort((a, b) => BOM_STATUS_SORT_ORDER[a.effectiveStatus] - BOM_STATUS_SORT_ORDER[b.effectiveStatus]);

    // Counts for filter bar
    const countOk = rows.filter(r => r.effectiveStatus === "ok").length;
    const countShort = rows.filter(r => r.effectiveStatus === "short" || r.effectiveStatus === "manual-short" || r.effectiveStatus === "confirmed-short").length;
    const countPossible = rows.filter(r => r.effectiveStatus === "possible").length;
    const countMissing = rows.filter(r => r.effectiveStatus === "missing").length;
    const countManual = rows.filter(r => r.effectiveStatus === "manual" || r.effectiveStatus === "manual-short").length;
    const countConfirmed = rows.filter(r => r.effectiveStatus === "confirmed" || r.effectiveStatus === "confirmed-short").length;
    const countDnp = rows.filter(r => r.effectiveStatus === "dnp").length;
    const total = rows.length;

    // Filter bar
    const filterBar = document.createElement("div");
    filterBar.className = "filter-bar";
    filterBar.innerHTML = `
      <button class="filter-btn${activeFilter === "all" ? " active" : ""}" data-filter="all">All (${total})</button>
      ${countManual > 0 ? `<button class="filter-btn${activeFilter === "manual" ? " active" : ""}" data-filter="manual">Manual (${countManual})</button>` : ''}
      ${countConfirmed > 0 ? `<button class="filter-btn${activeFilter === "confirmed" ? " active" : ""}" data-filter="confirmed">Confirmed (${countConfirmed})</button>` : ''}
      <button class="filter-btn${activeFilter === "ok" ? " active" : ""}" data-filter="ok">In Stock (${countOk})</button>
      <button class="filter-btn${activeFilter === "short" ? " active" : ""}" data-filter="short">Short (${countShort})</button>
      <button class="filter-btn${activeFilter === "possible" ? " active" : ""}" data-filter="possible">Possible (${countPossible})</button>
      <button class="filter-btn${activeFilter === "missing" ? " active" : ""}" data-filter="missing">Missing (${countMissing})</button>
      ${countDnp > 0 ? `<button class="filter-btn${activeFilter === "dnp" ? " active" : ""}" data-filter="dnp">DNP (${countDnp})</button>` : ''}
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
      <th style="width:78px;text-align:center">Match</th>
      <th></th>
    </tr></thead>`;

    const tbody = document.createElement("tbody");

    sortedRows.forEach(r => {
      const st = r.effectiveStatus;
      if (activeFilter !== "all" && st !== activeFilter) {
        const matchesFilter =
          (activeFilter === "manual" && st === "manual-short") ||
          (activeFilter === "confirmed" && st === "confirmed-short") ||
          (activeFilter === "short" && (st === "manual-short" || st === "confirmed-short"));
        if (!matchesFilter) return;
      }

      // Query filter: search across BOM + inventory fields
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

      // Inv qty with alt badge
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
      const linkBtnHtml = r.inv ? `<button class="link-btn${linkingMode && linkingInvItem === r.inv ? ' active' : ''}" title="Link to missing BOM row">Link</button>` : '';
      const isLinkingSource = linkingMode && linkingInvItem === r.inv;
      tr.innerHTML = `
        <td class="status">${icon}</td>
        <td class="mono">${escHtml(dispLcsc)}</td>
        <td class="mono" title="${escHtml(dispMpn)}">${escHtml(dispMpn)}</td>
        <td class="${qtyClass}" style="text-align:right;font-weight:600">${r.effectiveQty}</td>
        <td class="inv-qty-cell ${qtyClass}" style="text-align:right;font-weight:600">${haveHtml}</td>
        <td class="${st === 'missing' ? 'muted' : ''}">${escHtml(invDesc)}</td>
        <td class="mono" style="text-align:center">${matchLabel}</td>
        <td class="btn-group">${confirmBtnHtml}${adjBtnHtml}${linkBtnHtml}</td>
      `;
      if (isLinkingSource) tr.classList.add("linking-source");

      // Confirm button (if possible match)
      const confirmBtn = tr.querySelector(".confirm-btn");
      if (confirmBtn) {
        confirmBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          confirmMatch(r);
        });
      }

      // Unconfirm button (if confirmed match)
      const unconfirmBtn = tr.querySelector(".unconfirm-btn");
      if (unconfirmBtn) {
        unconfirmBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          unconfirmMatch(r);
        });
      }

      // Adjust button (if matched to inventory)
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
            EventBus.emit(Events.LINKING_MODE, { active: true, invItem: r.inv });
          });
        }
      }

      tbody.appendChild(tr);

      // Alt sub-rows if expanded
      if (r.alts && r.alts.length > 0 && expandedAlts.has(partKey)) {
        renderAltRows(tbody, r.alts, partKey, st, r);
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
        const pk = invPartKey(r.inv).toUpperCase();
        if (pk) matchedInvKeys.add(pk);
      }
    });

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

  function renderAltRows(tbody, alts, partKey, parentStatus, parentBom) {
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

        const isSource = linkingMode && linkingInvItem === item;
        const linkBtnStr = bomData ? `<button class="link-btn${isSource ? ' active' : ''}" title="Link to missing BOM row">Link</button>` : '';
        const valueStr = stockValue > 0 ? "$" + stockValue.toFixed(2) : "\u2014";
        row.innerHTML = `
          <span class="part-id">${escHtml(displayId)}</span>
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
            EventBus.emit(Events.LINKING_MODE, { active: true, invItem: item });
          });
        }
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
  const adjUnitPrice = document.getElementById("adj-unit-price");
  const adjExtPrice = document.getElementById("adj-ext-price");
  let currentPart = null;

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
    modal.classList.remove("hidden");
    adjQty.focus();
    adjQty.select();
  }

  // Auto-calc: adj unit → ext
  adjUnitPrice.addEventListener("input", () => {
    if (!currentPart) return;
    const up = parseFloat(adjUnitPrice.value);
    if (!isNaN(up) && currentPart.qty > 0) {
      adjExtPrice.value = (up * currentPart.qty).toFixed(2);
    }
  });

  // Auto-calc: adj ext → unit
  adjExtPrice.addEventListener("input", () => {
    if (!currentPart) return;
    const ep = parseFloat(adjExtPrice.value);
    if (!isNaN(ep) && currentPart.qty > 0) {
      adjUnitPrice.value = (ep / currentPart.qty).toFixed(4);
    }
  });

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

    try {
      // Apply qty adjustment
      const qtyResult = await api("adjust_part", type, pk, qty, note);
      if (qtyResult.error) {
        showToast("Error: " + qtyResult.error);
        return;
      }

      // Apply price update if changed
      if (priceChanged) {
        const up = !isNaN(newUp) ? newUp : null;
        const ep = !isNaN(newEp) ? newEp : null;
        const priceResult = await api("update_part_price", pk, up, ep);
        if (priceResult.error) {
          showToast("Qty adjusted, but price error: " + priceResult.error);
          AppLog.error("Price update failed: " + priceResult.error);
          onInventoryUpdated(qtyResult);  // Use the good qty result
          closeAdjustModal();
          return;
        }
        onInventoryUpdated(priceResult);
      } else {
        onInventoryUpdated(qtyResult);
      }

      closeAdjustModal();
      showToast("Adjusted " + pk);
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

  // ── Price Modal ──
  const priceModal = document.getElementById("price-modal");
  const priceTitle = document.getElementById("price-modal-title");
  const priceSubtitle = document.getElementById("price-modal-subtitle");
  const priceUnitInput = document.getElementById("price-unit");
  const priceExtInput = document.getElementById("price-ext");
  let pricePart = null;

  function openPriceModal(item) {
    pricePart = item;
    const pk = invPartKey(item);
    priceTitle.textContent = pk + (item.mpn && item.lcsc ? " \u2014 " + item.mpn : "");
    priceSubtitle.textContent = (item.description || item.package || "") + " (qty: " + item.qty + ")";
    priceUnitInput.value = item.unit_price > 0 ? item.unit_price : "";
    priceExtInput.value = item.ext_price > 0 ? item.ext_price : "";
    priceModal.classList.remove("hidden");
    priceUnitInput.focus();
  }

  function closePriceModal() {
    priceModal.classList.add("hidden");
    pricePart = null;
  }

  // Auto-calc: unit → ext
  priceUnitInput.addEventListener("input", () => {
    if (!pricePart) return;
    const up = parseFloat(priceUnitInput.value);
    if (!isNaN(up) && pricePart.qty > 0) {
      priceExtInput.value = (up * pricePart.qty).toFixed(2);
    }
  });

  // Auto-calc: ext → unit
  priceExtInput.addEventListener("input", () => {
    if (!pricePart) return;
    const ep = parseFloat(priceExtInput.value);
    if (!isNaN(ep) && pricePart.qty > 0) {
      priceUnitInput.value = (ep / pricePart.qty).toFixed(4);
    }
  });

  document.getElementById("price-cancel").addEventListener("click", closePriceModal);
  priceModal.addEventListener("click", (e) => {
    if (e.target === priceModal) closePriceModal();
  });

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
    try {
      const fresh = await api("update_part_price", pk, up, ep);
      if (fresh.error) {
        showToast("Error: " + fresh.error);
      } else {
        closePriceModal();
        onInventoryUpdated(fresh);
        showToast("Price updated for " + pk);
      }
    } catch (e) {
      showToast("Error: " + e.message);
    }
  });

  document.addEventListener("keydown", (e) => {
    if (priceModal.classList.contains("hidden")) return;
    if (e.key === "Escape") closePriceModal();
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
    linkingMode = false;
    linkingInvItem = null;
    App.manualLinks = [];
    App.confirmedMatches = [];
    render();
  });

  EventBus.on(Events.LINKING_MODE, (data) => {
    linkingMode = data.active;
    linkingInvItem = data.active ? data.invItem : null;
    render();
  });

  // ── Confirm Match Functions ──

  function confirmMatch(bomRow) {
    const bk = bomKey(bomRow.bom);
    const ipk = invPartKey(bomRow.inv);
    if (!bk || !ipk) return;
    App.confirmedMatches = App.confirmedMatches.filter(c => c.bomKey !== bk);
    App.confirmedMatches.push({ bomKey: bk, invPartKey: ipk });
    AppLog.info("Confirmed: " + bk + " \u2192 " + ipk);
    EventBus.emit(Events.CONFIRMED_CHANGED);
    showToast("Confirmed " + bk);
  }

  function unconfirmMatch(bomRow) {
    const bk = bomKey(bomRow.bom);
    if (!bk) return;
    App.confirmedMatches = App.confirmedMatches.filter(c => c.bomKey !== bk);
    AppLog.info("Unconfirmed: " + bk);
    EventBus.emit(Events.CONFIRMED_CHANGED);
    showToast("Unconfirmed " + bk);
  }

  function confirmAltMatch(bomRow, altInvItem) {
    const bk = bomKey(bomRow.bom);
    const ipk = invPartKey(altInvItem);
    if (!bk || !ipk) return;
    App.confirmedMatches = App.confirmedMatches.filter(c => c.bomKey !== bk);
    App.confirmedMatches.push({ bomKey: bk, invPartKey: ipk });
    AppLog.info("Confirmed alt: " + bk + " \u2192 " + ipk);
    EventBus.emit(Events.CONFIRMED_CHANGED);
    showToast("Confirmed " + bk + " \u2192 " + ipk);
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && linkingMode) {
      EventBus.emit(Events.LINKING_MODE, { active: false });
    }
  });
})();
