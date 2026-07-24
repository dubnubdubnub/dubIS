/* part-preview.js — Hover tooltip for LCSC, Digikey, Pololu, and Mouser part numbers.
   Shows product details fetched from APIs on hover over [data-lcsc], [data-digikey], [data-pololu], or [data-mouser] elements. */

import { api, AppLog } from './api.js';
import { formatMoney } from './ui-helpers.js';
import { html, el } from './dom/html.js';
import { innerRect, zoomedViewport } from './ui-zoom.js';

var HOVER_DELAY_MS = 300;
var HIDE_DELAY_MS = 150;
var LCSC_PART_REGEX = /^C\d{4,}$/i;

// JS-side cache: { lcsc: { code -> data|null }, digikey: { code -> data|null }, pololu: { sku -> data|null } }
var cache = { lcsc: {}, digikey: {}, pololu: {}, mouser: {} };

// Current state
var currentCode = null;
var currentProvider = null;
var showTimer = null;
var hideTimer = null;
var mouseDownInTooltip = false;

var tooltip = null;

function hasSelectionInTooltip() {
  var sel = window.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return false;
  // Use anchorNode/focusNode (drag endpoints) instead of range.startContainer
  // (DOM order) — dragging from tooltip outward creates a backward range whose
  // startContainer is outside the tooltip.
  return tooltip.contains(sel.anchorNode) || tooltip.contains(sel.focusNode);
}

export function init() {
  // ── Tooltip element ──
  tooltip = document.createElement("div");
  tooltip.className = "part-preview hidden";
  document.body.appendChild(tooltip);

  tooltip.addEventListener("mouseenter", function () {
    clearTimeout(hideTimer);
  });
  tooltip.addEventListener("mouseleave", function () {
    if (!mouseDownInTooltip) scheduleHide();
  });
  tooltip.addEventListener("mousedown", function () {
    mouseDownInTooltip = true;
  });
  document.addEventListener("mouseup", function () {
    if (mouseDownInTooltip) {
      mouseDownInTooltip = false;
      // If mouse is outside the tooltip after releasing, schedule hide
      // (unless there's a text selection inside the tooltip)
      if (!tooltip.matches(":hover") && !hasSelectionInTooltip()) {
        scheduleHide();
      }
    }
  });

  // ── Event delegation ──

  document.addEventListener("mouseover", function (e) {
    var trigger = e.target.closest("[data-lcsc], [data-digikey], [data-pololu], [data-mouser]");
    if (!trigger) return;

    var provider, code;
    if (trigger.dataset.lcsc) {
      provider = "lcsc";
      code = (trigger.dataset.lcsc || "").trim().toUpperCase();
      if (!LCSC_PART_REGEX.test(code)) return;
    } else if (trigger.dataset.digikey) {
      provider = "digikey";
      code = (trigger.dataset.digikey || "").trim();
      if (!code) return;
    } else if (trigger.dataset.pololu) {
      provider = "pololu";
      code = (trigger.dataset.pololu || "").trim();
      if (!code) return;
    } else {
      provider = "mouser";
      code = (trigger.dataset.mouser || "").trim();
      if (!code) return;
    }

    clearTimeout(hideTimer);
    clearTimeout(showTimer);

    showTimer = setTimeout(function () {
      showTooltip(code, provider, trigger);
    }, HOVER_DELAY_MS);
  });

  document.addEventListener("mouseout", function (e) {
    var trigger = e.target.closest("[data-lcsc], [data-digikey], [data-pololu], [data-mouser]");
    if (!trigger) return;
    clearTimeout(showTimer);
    scheduleHide();
  });

  // Re-evaluate hide when the user clears a selection (e.g. clicks elsewhere)
  // while the mouse is already outside the tooltip.
  document.addEventListener("selectionchange", function () {
    if (tooltip.classList.contains("hidden")) return;
    if (mouseDownInTooltip) return;
    if (hasSelectionInTooltip()) return;
    if (tooltip.matches(":hover")) return;
    scheduleHide();
  });
}

function scheduleHide() {
  if (mouseDownInTooltip || hasSelectionInTooltip()) return;
  clearTimeout(hideTimer);
  hideTimer = setTimeout(function () {
    tooltip.classList.add("hidden");
    currentCode = null;
    currentProvider = null;
  }, HIDE_DELAY_MS);
}

// ── Show tooltip ──

async function showTooltip(code, provider, triggerEl) {
  currentCode = code;
  currentProvider = provider;
  tooltip.classList.remove("hidden");
  positionTooltip(triggerEl);

  // Show loading state
  tooltip.replaceChildren(html`<div class="part-preview-card"><div class="part-preview-loading">Loading ${code}...</div></div>`);

  var data = await fetchProduct(code, provider);

  // Stale check — user may have moved away
  if (currentCode !== code || currentProvider !== provider) return;

  if (!data) {
    var errMsg = "Product not found";
    if (provider === "digikey") {
      var dkStatus = await api("get_digikey_login_status");
      errMsg = (dkStatus && dkStatus.logged_in)
        ? "Could not load product data"
        : "Login to Digikey in Preferences to enable preview";
    } else if (provider === "mouser") {
      var moStatus = await api("get_mouser_api_key_status");
      errMsg = (moStatus && moStatus.configured)
        ? "Mouser API: no match for " + code
        : "Save a Mouser API key in Preferences for tooltips";
    }
    tooltip.replaceChildren(html`<div class="part-preview-card"><div class="part-preview-error">${errMsg}</div></div>`);
    return;
  }

  renderTooltip(data, provider);
  // Re-position after render (content may have changed height)
  positionTooltip(triggerEl);

  // Record price observation (fire-and-forget)
  if (data.prices && data.prices.length > 0) {
    api("record_fetched_prices", code, provider, data.prices).catch(function () { /* ignore */ });
  }

  // Fetch and display price history (fire-and-forget)
  api("get_price_summary", code).then(function (summary) {
    if (currentCode !== code || currentProvider !== provider) return;
    if (summary && Object.keys(summary).length > 0) {
      appendPriceHistory(summary);
      positionTooltip(triggerEl);
    }
  }).catch(function () { /* ignore */ });

  // Fetch and display part adjustment history (fire-and-forget)
  api("get_part_history", code).then(function (entries) {
    if (currentCode !== code || currentProvider !== provider) return;
    appendPartHistory(entries || []);
    positionTooltip(triggerEl);
  }).catch(function (e) { AppLog.warn("get_part_history failed for " + code + ": " + e); });
}

// ── Fetch with cache ──

async function fetchProduct(code, provider) {
  if (code in cache[provider]) return cache[provider][code];
  var method = provider === "lcsc" ? "fetch_lcsc_product" : provider === "digikey" ? "fetch_digikey_product" : provider === "pololu" ? "fetch_pololu_product" : "fetch_mouser_product";
  try {
    var data = await api(method, code);
    cache[provider][code] = data || null;
    return cache[provider][code];
  } catch (err) {
    AppLog.warn(provider.toUpperCase() + " preview fetch failed for " + code + ": " + err);
    cache[provider][code] = null;
    return null;
  }
}

// ── Position tooltip ──

function positionTooltip(triggerEl) {
  // innerRect + zoomedViewport, not getBoundingClientRect + window.innerWidth:
  // under the root UI zoom, rects read back post-zoom while offsetHeight and the
  // px we write below are pre-zoom. Mixing the two mispositions the tooltip at
  // any zoom != 1 while looking perfect at 100%. See js/ui-zoom.js.
  var rect = innerRect(triggerEl);
  var vp = zoomedViewport();
  var tw = 360;
  var th = tooltip.offsetHeight || 200;

  // Prefer below the trigger
  var top = rect.bottom + 6;
  var left = rect.left;

  // Clamp horizontally
  if (left + tw > vp.w - 8) {
    left = vp.w - tw - 8;
  }
  if (left < 8) left = 8;

  // If not enough space below, show above
  if (top + th > vp.h - 8) {
    top = rect.top - th - 6;
  }
  // Clamp vertically
  if (top < 8) top = 8;

  tooltip.style.left = left + "px";
  tooltip.style.top = top + "px";
}

// ── Render tooltip content ──

function renderTooltip(data, provider) {
  var providerClass = "provider-" + provider;
  var parts = [];

  // Product image
  if (data.imageUrl) {
    parts.push(html`<div class="part-preview-img"><img src="${data.imageUrl}" alt="Product"></div>`);
  }

  // Title (product name like "Raspberry Pi RP2040")
  if (data.title) {
    parts.push(html`<div class="part-preview-title">${data.title}</div>`);
  }

  // Description (spec summary)
  if (data.description && data.description !== data.title) {
    parts.push(html`<div class="part-preview-desc">${data.description}</div>`);
  }

  // Info table
  var infoRows = [];
  if (data.manufacturer) infoRows.push(infoRow("Manufacturer", data.manufacturer));
  if (data.mpn) infoRows.push(infoRow("Mfr. Part #", data.mpn));

  var partLabel = provider === "lcsc" ? "LCSC Part #" : provider === "digikey" ? "Digikey Part #" : provider === "pololu" ? "Pololu SKU" : "Mouser Part #";
  infoRows.push(infoRow(partLabel, data.productCode));

  if (data.package) infoRows.push(infoRow("Package", data.package));
  if (data.category) {
    var catText = data.category + (data.subcategory ? " > " + data.subcategory : "");
    infoRows.push(infoRow("Category", catText));
  }
  parts.push(html`<table class="part-preview-info"><tbody>${infoRows}</tbody></table>`);

  // Key attributes
  if (data.attributes && data.attributes.length > 0) {
    var attrEls = data.attributes.map(function (a) {
      return html`<span class="part-preview-attr">${a.name}: <strong>${a.value}</strong></span>`;
    });
    parts.push(html`<div class="part-preview-attrs">${attrEls}</div>`);
  }

  // Stock badge
  var stockNum = typeof data.stock === "number" ? data.stock : 0;
  var stockClass = stockNum > 0 ? "in-stock" : "no-stock";
  var stockLabel = stockNum > 0 ? stockNum.toLocaleString() + " in stock" : "Out of stock";
  parts.push(html`<div class="part-preview-stock ${stockClass}">${stockLabel}</div>`);

  // Packaging tabs (only for sources with multiple packaging variants — Digikey).
  // Each tab swaps the visible price table without re-fetching.
  var hasPackagings = Array.isArray(data.packagings) && data.packagings.length > 1;
  var activeIdx = 0;
  if (hasPackagings) {
    activeIdx = pickActivePackagingIdx(data.packagings, data.productCode);
    var tabEls = data.packagings.map(function (pkg, idx) {
      var cls = "part-preview-pack-tab" + (idx === activeIdx ? " active" : "");
      return el("button", { type: "button", class: cls, "data-pack-idx": String(idx) }, pkg.name || ("Packaging " + (idx + 1)));
    });
    parts.push(html`<div class="part-preview-pack-tabs" role="tablist">${tabEls}</div>`);
    parts.push(html`<div class="part-preview-prices-wrap">${renderPriceTable(data.packagings[activeIdx].prices || [])}</div>`);
  } else if (data.prices && data.prices.length > 0) {
    parts.push(renderPriceTable(data.prices));
  }

  // Action links: datasheet + provider page
  var actionLinks = [];
  if (data.pdfUrl) {
    actionLinks.push(html`<a class="part-preview-link" href="${data.pdfUrl}" target="_blank">Datasheet (PDF)</a>`);
  }
  var pageUrl = data.lcscUrl || data.digikeyUrl || data.pololuUrl || data.mouserUrl || "";
  var pageName = provider === "lcsc" ? "LCSC" : provider === "digikey" ? "Digikey" : provider === "pololu" ? "Pololu" : "Mouser";
  if (pageUrl) {
    actionLinks.push(html`<a class="part-preview-link" href="${pageUrl}" target="_blank">View on ${pageName} &rarr;</a>`);
  }
  parts.push(html`<div class="part-preview-actions">${actionLinks}</div>`);

  if (data._debug) {
    parts.push(html`<div class="part-preview-debug"><div class="part-preview-debug-bar"><button class="part-preview-debug-toggle">Raw data &darr;</button><button class="part-preview-copy-btn">Copy JSON</button></div><pre class="part-preview-debug-json hidden">${JSON.stringify(data._debug, null, 2)}</pre></div>`);
  }

  tooltip.replaceChildren(el("div", { class: "part-preview-card " + providerClass }, ...parts));

  var toggleBtn = tooltip.querySelector(".part-preview-debug-toggle");
  if (toggleBtn) {
    toggleBtn.addEventListener("click", function () {
      var pre = tooltip.querySelector(".part-preview-debug-json");
      if (pre) {
        var collapsed = pre.classList.toggle("hidden");
        toggleBtn.replaceChildren(collapsed ? html`Raw data &darr;` : html`Raw data &uarr;`);
      }
    });
  }

  var copyBtn = tooltip.querySelector(".part-preview-copy-btn");
  if (copyBtn) {
    copyBtn.addEventListener("click", function () {
      var pre = tooltip.querySelector(".part-preview-debug-json");
      if (pre) {
        navigator.clipboard.writeText(pre.textContent).then(function () {
          copyBtn.textContent = "Copied!";
          setTimeout(function () { copyBtn.textContent = "Copy JSON"; }, 1500);
        });
      }
    });
  }

  if (hasPackagings) {
    var tabs = tooltip.querySelectorAll(".part-preview-pack-tab");
    var pricesWrap = tooltip.querySelector(".part-preview-prices-wrap");
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        var idx = parseInt(tab.getAttribute("data-pack-idx") || "0", 10);
        if (!Number.isFinite(idx) || !data.packagings[idx]) return;
        tabs.forEach(function (t) { t.classList.remove("active"); });
        tab.classList.add("active");
        if (pricesWrap) {
          pricesWrap.replaceChildren(renderPriceTable(data.packagings[idx].prices || []));
        }
      });
    });
  }
}

function renderPriceTable(prices) {
  if (!prices || prices.length === 0) {
    return html`<div class="part-preview-no-prices">No pricing available</div>`;
  }
  var rows = prices.map(function (p) {
    var qty = Number(p.qty) || 0;
    var unit = Number(p.price) || 0;
    var ext = qty * unit;
    return html`<tr><td>${String(p.qty)}+</td><td>$${unit.toFixed(4)}</td><td>${formatMoney(ext)}</td></tr>`;
  });
  return html`<table class="part-preview-prices"><thead><tr><th>Qty</th><th>Unit Price</th><th>Ext. Price</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function pickActivePackagingIdx(packagings, productCode) {
  var pn = (productCode || "").toString().trim().toUpperCase();
  for (var i = 0; i < packagings.length; i++) {
    var pkgPn = (packagings[i].partNumber || "").toString().trim().toUpperCase();
    if (pkgPn && pkgPn === pn) return i;
  }
  // Fallback: code suffix match (e.g. requested ends in TR-ND, code=TR)
  var m = pn.match(/([A-Z]{2,4})-ND\b/);
  if (m) {
    for (var j = 0; j < packagings.length; j++) {
      if ((packagings[j].code || "").toString().toUpperCase() === m[1]) return j;
    }
  }
  return 0;
}

function appendPriceHistory(summary) {
  var card = tooltip.querySelector(".part-preview-card");
  if (!card) return;
  var rows = [];
  for (var dist in summary) {
    var s = summary[dist];
    rows.push(html`<tr><td>${dist}</td><td>$${Number(s.latest_unit_price || 0).toFixed(4)}</td><td>$${Number(s.avg_unit_price || 0).toFixed(4)}</td><td>${s.price_count || 0}</td></tr>`);
  }
  var node = html`<div class="part-preview-history"><div class="part-preview-history-title">Price History</div><table class="part-preview-prices"><thead><tr><th>Source</th><th>Latest</th><th>Avg</th><th>#</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  // Insert before debug section if present, otherwise append
  var debug = card.querySelector(".part-preview-debug");
  if (debug) debug.before(node);
  else card.appendChild(node);
}

function appendPartHistory(entries) {
  var card = tooltip.querySelector(".part-preview-card");
  if (!card) return;
  var body;
  if (!entries || entries.length === 0) {
    body = html`<div class="part-preview-no-history">No adjustments recorded</div>`;
  } else {
    var recent = entries.slice(-8); // show the 8 most recent entries
    var rows = recent.map(function (e) {
      var date = (e.timestamp || "").slice(0, 10);
      var src = e.source || "—";
      var delta = typeof e.qty_delta === "number" ? e.qty_delta : parseInt(e.qty_delta, 10) || 0;
      var sign = delta > 0 ? "+" : "";
      var kindLabel = e.kind === "set" ? "set" : (e.kind || "");
      var deltaText = kindLabel === "set" ? "→" + delta : sign + delta;
      return html`<tr><td>${date}</td><td>${src}</td><td>${deltaText}</td></tr>`;
    });
    body = html`<table class="part-preview-prices"><tbody>${rows}</tbody></table>`;
  }
  var node = html`<div class="part-preview-history part-preview-adj-history"><div class="part-preview-history-title">History</div>${body}</div>`;
  // Insert before debug section if present, otherwise append
  var debug = card.querySelector(".part-preview-debug");
  if (debug) debug.before(node);
  else card.appendChild(node);
}

function infoRow(label, value) {
  return html`<tr><td class="label">${label}</td><td>${value}</td></tr>`;
}
