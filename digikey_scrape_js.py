"""Digikey in-browser JS scrape payload for product data extraction."""

from __future__ import annotations

# JS scrape that runs inside the hidden Digikey browser. Gathers structured
# data (JSON-LD / __NEXT_DATA__) and falls back to DOM scraping for fields
# that are typically only rendered (price tiers, packaging variants,
# datasheet link). Always returns a combined envelope so the normalizer can
# merge sources.
SCRAPE_JS = r"""
(function () {
  function findProduct(obj) {
    if (!obj || typeof obj !== 'object') return null;
    if (obj['@type'] === 'Product') return obj;
    if (Array.isArray(obj)) {
      for (var i = 0; i < obj.length; i++) {
        var r = findProduct(obj[i]);
        if (r) return r;
      }
    }
    if (Array.isArray(obj['@graph'])) {
      for (var j = 0; j < obj['@graph'].length; j++) {
        var r2 = findProduct(obj['@graph'][j]);
        if (r2) return r2;
      }
    }
    return null;
  }

  function jsonLdProduct() {
    var scripts = document.querySelectorAll('script[type="application/ld+json"]');
    for (var i = 0; i < scripts.length; i++) {
      try {
        var ld = JSON.parse(scripts[i].textContent);
        var prod = findProduct(ld);
        if (prod) return prod;
      } catch (e) {}
    }
    return null;
  }

  function nextDataProps() {
    var ndEl = document.getElementById('__NEXT_DATA__');
    if (!ndEl) return null;
    try {
      var nd = JSON.parse(ndEl.textContent);
      return (nd && nd.props && nd.props.pageProps) || null;
    } catch (e) { return null; }
  }

  function rscDetected() {
    var scripts = document.querySelectorAll('script');
    for (var k = 0; k < scripts.length; k++) {
      var txt = scripts[k].textContent || '';
      if (txt.indexOf('self.__next_f.push') !== -1) return true;
    }
    return false;
  }

  // ── DOM scraping ────────────────────────────────────────

  function scrapePriceTiers() {
    // Find a <table> whose body rows look like [qty, $unit, $extended].
    // Heuristic: at least 2 rows where col0 is a quantity and col1 is a $ amount.
    var tables = document.querySelectorAll('table');
    var best = [];
    for (var t = 0; t < tables.length; t++) {
      var rows = tables[t].querySelectorAll('tr');
      var tiers = [];
      for (var r = 0; r < rows.length; r++) {
        var cells = rows[r].querySelectorAll('td');
        if (cells.length < 2) continue;
        var qtyText = (cells[0].textContent || '').replace(/[^0-9]/g, '');
        var priceMatch = (cells[1].textContent || '').match(/\$?\s*(\d+(?:\.\d+)?)/);
        if (qtyText && priceMatch) {
          var qty = parseInt(qtyText, 10);
          var price = parseFloat(priceMatch[1]);
          if (qty > 0 && price > 0) tiers.push({ qty: qty, price: price });
        }
      }
      if (tiers.length > best.length) best = tiers;
    }
    return best;
  }

  function scrapeDatasheetUrl() {
    var links = document.querySelectorAll('a');
    // Prefer an anchor whose text mentions datasheet AND points at a PDF.
    for (var i = 0; i < links.length; i++) {
      var text = (links[i].textContent || '').toLowerCase();
      var href = links[i].href || '';
      if ((text.indexOf('datasheet') !== -1 || text.indexOf('data sheet') !== -1) &&
          /\.pdf(\b|\?|$)/i.test(href)) {
        return href;
      }
    }
    // Fallback: aria-label or title attribute hints
    for (var j = 0; j < links.length; j++) {
      var aria = ((links[j].getAttribute('aria-label') || '') + ' ' +
                  (links[j].getAttribute('title') || '')).toLowerCase();
      var href2 = links[j].href || '';
      if (aria.indexOf('datasheet') !== -1 && /\.pdf(\b|\?|$)/i.test(href2)) {
        return href2;
      }
    }
    return '';
  }

  function scrapePackagings() {
    // Heuristic: links/buttons with text like "Cut Tape (CT)" or
    // "Tape & Reel (TR)" — DK uses these as alternate-packaging links.
    var pkgWordRe = /(Cut\s*Tape|Tape\s*&\s*Reel|Tape\s*&\s*Box|Tray|Tube|Bulk|Strip|Reel|Bag|Each)/i;
    var pkgPattern = /^([A-Za-z][A-Za-z0-9 &/\-]{1,30})\s*\(([A-Z]{1,5})\)\s*$/;
    var els = document.querySelectorAll('a, button, td, span, div');
    var seen = {};
    var packagings = [];
    for (var i = 0; i < els.length; i++) {
      // Only consider leaf-ish elements (avoid huge container text matching).
      if (els[i].children && els[i].children.length > 0) {
        // Allow if it's a link/button (treat as leaf even with icon children)
        var tag = (els[i].tagName || '').toLowerCase();
        if (tag !== 'a' && tag !== 'button') continue;
      }
      var t = (els[i].textContent || '').trim();
      if (t.length === 0 || t.length > 40) continue;
      var m = t.match(pkgPattern);
      if (!m) continue;
      if (!pkgWordRe.test(m[1])) continue;
      var label = m[1].trim() + ' (' + m[2] + ')';
      if (seen[label]) continue;
      seen[label] = true;
      var href = els[i].href || els[i].getAttribute('href') || '';
      packagings.push({ name: label, code: m[2], href: href });
    }
    return packagings;
  }

  function scrapeStock() {
    var bt = document.body && document.body.innerText || '';
    var sm = bt.match(/(\d[\d,]*)\s+In\s*Stock/i);
    if (sm) return parseInt(sm[1].replace(/,/g, ''), 10);
    return 0;
  }

  // ── Build envelope ──────────────────────────────────────

  var jsonld = jsonLdProduct();
  var nextdata = nextDataProps();
  var rsc = rscDetected();

  var dom = {
    priceTiers: scrapePriceTiers(),
    packagings: scrapePackagings(),
    datasheetUrl: scrapeDatasheetUrl(),
    stock: scrapeStock()
  };

  // If we got nothing structured AND DOM is empty, return diagnostic.
  if (!jsonld && !nextdata &&
      dom.priceTiers.length === 0 &&
      !dom.datasheetUrl &&
      dom.packagings.length === 0) {
    return {
      _source: 'diag',
      _reason: rsc ? 'next_app_router_rsc' : 'no_product_data',
      _url: window.location.href,
      _title: document.title,
      _hasJsonLd: !!document.querySelector('script[type="application/ld+json"]'),
      _hasNextData: !!document.getElementById('__NEXT_DATA__'),
      _scriptCount: document.querySelectorAll('script').length
    };
  }

  return {
    _source: 'dk_combined',
    jsonld: jsonld,
    nextdata: nextdata,
    rsc: rsc,
    dom: dom,
    _url: window.location.href,
    _title: document.title
  };
})()
"""
