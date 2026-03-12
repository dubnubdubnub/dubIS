/* part-preview.js — Hover tooltip for LCSC and Digikey part numbers.
   Shows product details fetched from APIs on hover over [data-lcsc] or [data-digikey] elements. */

(function () {
  var HOVER_DELAY_MS = 300;
  var HIDE_DELAY_MS = 150;
  var LCSC_PART_REGEX = /^C\d{4,}$/i;

  // JS-side cache: { lcsc: { code -> data|null }, digikey: { code -> data|null } }
  var cache = { lcsc: {}, digikey: {} };

  // Current state
  var currentCode = null;
  var currentProvider = null;
  var showTimer = null;
  var hideTimer = null;

  // ── Tooltip element ──

  var tooltip = document.createElement("div");
  tooltip.className = "part-preview hidden";
  document.body.appendChild(tooltip);

  tooltip.addEventListener("mouseenter", function () {
    clearTimeout(hideTimer);
  });
  tooltip.addEventListener("mouseleave", function () {
    scheduleHide();
  });

  // ── Event delegation ──

  document.addEventListener("mouseover", function (e) {
    var trigger = e.target.closest("[data-lcsc], [data-digikey]");
    if (!trigger) return;

    var provider, code;
    if (trigger.dataset.lcsc) {
      provider = "lcsc";
      code = (trigger.dataset.lcsc || "").trim().toUpperCase();
      if (!LCSC_PART_REGEX.test(code)) return;
    } else {
      provider = "digikey";
      code = (trigger.dataset.digikey || "").trim();
      if (!code) return;
    }

    clearTimeout(hideTimer);
    clearTimeout(showTimer);

    showTimer = setTimeout(function () {
      showTooltip(code, provider, trigger);
    }, HOVER_DELAY_MS);
  });

  document.addEventListener("mouseout", function (e) {
    var trigger = e.target.closest("[data-lcsc], [data-digikey]");
    if (!trigger) return;
    clearTimeout(showTimer);
    scheduleHide();
  });

  function scheduleHide() {
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
    tooltip.innerHTML = '<div class="part-preview-card"><div class="part-preview-loading">Loading ' + escHtml(code) + '...</div></div>';

    var data = await fetchProduct(code, provider);

    // Stale check — user may have moved away
    if (currentCode !== code || currentProvider !== provider) return;

    if (!data) {
      var errMsg = "Product not found";
      if (provider === "digikey") {
        errMsg = "Login to Digikey in Preferences to enable preview";
      }
      tooltip.innerHTML = '<div class="part-preview-card"><div class="part-preview-error">' + escHtml(errMsg) + '</div></div>';
      return;
    }

    renderTooltip(data, provider);
    // Re-position after render (content may have changed height)
    positionTooltip(triggerEl);
  }

  // ── Fetch with cache ──

  async function fetchProduct(code, provider) {
    if (code in cache[provider]) return cache[provider][code];
    var method = provider === "lcsc" ? "fetch_lcsc_product" : "fetch_digikey_product";
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

  function renderTooltip(data, provider) {
    var providerClass = "provider-" + provider;
    var html = '<div class="part-preview-card ' + providerClass + '">';

    // Product image
    if (data.imageUrl) {
      html += '<div class="part-preview-img"><img src="' + escHtml(data.imageUrl) + '" alt="Product"></div>';
    }

    // Title (product name like "Raspberry Pi RP2040")
    if (data.title) {
      html += '<div class="part-preview-title">' + escHtml(data.title) + '</div>';
    }

    // Description (spec summary)
    if (data.description && data.description !== data.title) {
      html += '<div class="part-preview-desc">' + escHtml(data.description) + '</div>';
    }

    // Info table
    html += '<table class="part-preview-info">';
    if (data.manufacturer) html += infoRow("Manufacturer", data.manufacturer);
    if (data.mpn) html += infoRow("Mfr. Part #", data.mpn);

    var partLabel = provider === "lcsc" ? "LCSC Part #" : "Digikey Part #";
    html += infoRow(partLabel, data.productCode);

    if (data.package) html += infoRow("Package", data.package);
    if (data.category) {
      var catText = data.category + (data.subcategory ? " > " + data.subcategory : "");
      html += infoRow("Category", catText);
    }
    html += '</table>';

    // Key attributes
    if (data.attributes && data.attributes.length > 0) {
      html += '<div class="part-preview-attrs">';
      data.attributes.forEach(function (a) {
        html += '<span class="part-preview-attr">' + escHtml(a.name) + ': <strong>' + escHtml(a.value) + '</strong></span>';
      });
      html += '</div>';
    }

    // Stock badge
    var stockNum = typeof data.stock === "number" ? data.stock : 0;
    var stockClass = stockNum > 0 ? "in-stock" : "no-stock";
    var stockLabel = stockNum > 0 ? stockNum.toLocaleString() + " in stock" : "Out of stock";
    html += '<div class="part-preview-stock ' + stockClass + '">' + escHtml(stockLabel) + '</div>';

    // Price tiers
    if (data.prices && data.prices.length > 0) {
      html += '<table class="part-preview-prices">';
      html += '<thead><tr><th>Qty</th><th>Unit Price</th></tr></thead><tbody>';
      data.prices.forEach(function (p) {
        html += '<tr><td>' + escHtml(String(p.qty)) + '+</td><td>$' + escHtml(Number(p.price).toFixed(4)) + '</td></tr>';
      });
      html += '</tbody></table>';
    }

    // Action links: datasheet + provider page
    html += '<div class="part-preview-actions">';
    if (data.pdfUrl) {
      html += '<a class="part-preview-link" href="' + escHtml(data.pdfUrl) + '" target="_blank">Datasheet (PDF)</a>';
    }
    var pageUrl = data.lcscUrl || data.digikeyUrl || "";
    var pageName = provider === "lcsc" ? "LCSC" : "Digikey";
    if (pageUrl) {
      html += '<a class="part-preview-link" href="' + escHtml(pageUrl) + '" target="_blank">View on ' + pageName + ' &rarr;</a>';
    }
    html += '</div>';

    if (provider === "digikey" && data._debug) {
      html += '<div class="part-preview-debug">';
      html += '<div style="display:flex;align-items:center;justify-content:space-between;margin-top:8px;">';
      html += '<span style="font-size:11px;font-weight:600;color:#888;">Raw scraped data:</span>';
      html += '<button class="part-preview-copy-btn" style="font-size:10px;padding:2px 8px;border:1px solid #555;border-radius:3px;background:#2a2a2a;color:#ccc;cursor:pointer;">Copy JSON</button>';
      html += '</div>';
      html += '<pre class="part-preview-debug-json" style="max-height:200px;overflow:auto;font-size:10px;background:#1a1a1a;color:#ccc;padding:6px;border-radius:4px;white-space:pre-wrap;word-break:break-all;user-select:text;-webkit-user-select:text;">' + escHtml(JSON.stringify(data._debug, null, 2)) + '</pre>';
      html += '</div>';
    }

    html += '</div>';
    tooltip.innerHTML = html;

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
  }

  function infoRow(label, value) {
    return '<tr><td class="label">' + escHtml(label) + '</td><td>' + escHtml(value) + '</td></tr>';
  }
})();
