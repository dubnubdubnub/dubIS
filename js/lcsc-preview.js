/* lcsc-preview.js — Hover tooltip for LCSC part numbers.
   Shows product details fetched from LCSC API on hover over [data-lcsc] elements. */

(function () {
  var LCSC_HOVER_DELAY_MS = 300;
  var LCSC_HIDE_DELAY_MS = 150;
  var LCSC_PART_REGEX = /^C\d{4,}$/i;

  // JS-side cache: code -> product data | null (failure)
  var cache = {};

  // Current state
  var currentCode = null;
  var showTimer = null;
  var hideTimer = null;

  // ── Tooltip element ──

  var tooltip = document.createElement("div");
  tooltip.className = "lcsc-preview hidden";
  document.body.appendChild(tooltip);

  tooltip.addEventListener("mouseenter", function () {
    clearTimeout(hideTimer);
  });
  tooltip.addEventListener("mouseleave", function () {
    scheduleHide();
  });

  // ── Event delegation ──

  document.addEventListener("mouseover", function (e) {
    var trigger = e.target.closest("[data-lcsc]");
    if (!trigger) return;
    var code = (trigger.dataset.lcsc || "").trim().toUpperCase();
    if (!LCSC_PART_REGEX.test(code)) return;

    clearTimeout(hideTimer);
    clearTimeout(showTimer);

    showTimer = setTimeout(function () {
      showTooltip(code, trigger);
    }, LCSC_HOVER_DELAY_MS);
  });

  document.addEventListener("mouseout", function (e) {
    var trigger = e.target.closest("[data-lcsc]");
    if (!trigger) return;
    clearTimeout(showTimer);
    scheduleHide();
  });

  function scheduleHide() {
    clearTimeout(hideTimer);
    hideTimer = setTimeout(function () {
      tooltip.classList.add("hidden");
      currentCode = null;
    }, LCSC_HIDE_DELAY_MS);
  }

  // ── Show tooltip ──

  async function showTooltip(code, triggerEl) {
    currentCode = code;
    tooltip.classList.remove("hidden");
    positionTooltip(triggerEl);

    // Show loading state
    tooltip.innerHTML = '<div class="lcsc-preview-card"><div class="lcsc-preview-loading">Loading ' + escHtml(code) + '...</div></div>';

    var data = await fetchProduct(code);

    // Stale check — user may have moved away
    if (currentCode !== code) return;

    if (!data) {
      tooltip.innerHTML = '<div class="lcsc-preview-card"><div class="lcsc-preview-error">Product not found</div></div>';
      return;
    }

    renderTooltip(data);
    // Re-position after render (content may have changed height)
    positionTooltip(triggerEl);
  }

  // ── Fetch with cache ──

  async function fetchProduct(code) {
    if (code in cache) return cache[code];
    try {
      var data = await api("fetch_lcsc_product", code);
      cache[code] = data || null;
      return cache[code];
    } catch (err) {
      AppLog.warn("LCSC preview fetch failed for " + code + ": " + err);
      cache[code] = null;
      return null;
    }
  }

  // ── Position tooltip ──

  function positionTooltip(triggerEl) {
    var rect = triggerEl.getBoundingClientRect();
    var tw = 360;
    var th = tooltip.offsetHeight || 200;

    // Prefer below the trigger
    var top = rect.bottom + 6;
    var left = rect.left;

    // Clamp horizontally
    if (left + tw > window.innerWidth - 8) {
      left = window.innerWidth - tw - 8;
    }
    if (left < 8) left = 8;

    // If not enough space below, show above
    if (top + th > window.innerHeight - 8) {
      top = rect.top - th - 6;
    }
    // Clamp vertically
    if (top < 8) top = 8;

    tooltip.style.left = left + "px";
    tooltip.style.top = top + "px";
  }

  // ── Render tooltip content ──

  function renderTooltip(data) {
    var html = '<div class="lcsc-preview-card">';

    // Product image
    if (data.imageUrl) {
      html += '<div class="lcsc-preview-img"><img src="' + escHtml(data.imageUrl) + '" alt="Product"></div>';
    }

    // Title
    var title = (data.manufacturer || "") + (data.mpn ? " " + data.mpn : "");
    if (title.trim()) {
      html += '<div class="lcsc-preview-title">' + escHtml(title.trim()) + '</div>';
    }

    // Info table
    html += '<table class="lcsc-preview-info">';
    if (data.manufacturer) html += infoRow("Manufacturer", data.manufacturer);
    if (data.mpn) html += infoRow("Mfr. Part #", data.mpn);
    html += infoRow("LCSC Part #", data.productCode);
    if (data.package) html += infoRow("Package", data.package);
    if (data.description && data.description !== data.title) html += infoRow("Description", data.description);
    if (data.category) {
      var catText = data.category + (data.subcategory ? " > " + data.subcategory : "");
      html += infoRow("Category", catText);
    }
    html += '</table>';

    // Stock badge
    var stockNum = typeof data.stock === "number" ? data.stock : 0;
    var stockClass = stockNum > 0 ? "in-stock" : "no-stock";
    var stockLabel = stockNum > 0 ? stockNum.toLocaleString() + " in stock" : "Out of stock";
    html += '<div class="lcsc-preview-stock ' + stockClass + '">' + escHtml(stockLabel) + '</div>';

    // Price tiers
    if (data.prices && data.prices.length > 0) {
      html += '<table class="lcsc-preview-prices">';
      html += '<thead><tr><th>Qty</th><th>Unit Price</th></tr></thead><tbody>';
      data.prices.forEach(function (p) {
        html += '<tr><td>' + escHtml(String(p.qty)) + '+</td><td>$' + escHtml(Number(p.price).toFixed(4)) + '</td></tr>';
      });
      html += '</tbody></table>';
    }

    // LCSC link
    if (data.lcscUrl) {
      html += '<a class="lcsc-preview-link" href="' + escHtml(data.lcscUrl) + '" target="_blank">View on LCSC &rarr;</a>';
    }

    html += '</div>';
    tooltip.innerHTML = html;
  }

  function infoRow(label, value) {
    return '<tr><td class="label">' + escHtml(label) + '</td><td>' + escHtml(value) + '</td></tr>';
  }
})();
