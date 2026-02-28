/* matching.js — 5-step BOM matching engine (ported from build_compare.py) */

// ── Value parsing for possible matches ──

function getMult(c) {
  const m = {p:1e-12, n:1e-9, u:1e-6, U:1e-6, m:1e-3, R:1, k:1e3, K:1e3, M:1e6, G:1e9};
  if (m[c] != null) return m[c];
  if (c === '\u00b5' || c === '\u03bc') return 1e-6;
  return null;
}

function parseEEValue(str) {
  if (!str) return null;
  str = str.split(/[\/\u00b1%]/)[0].trim();
  str = str.replace(/[FH\u03a9\u2126]+$/i, '').replace(/ohm$/i, '').trim();
  var m = str.match(/^(\d+\.?\d*)([pnumkMGR\u00b5\u03bc])(\d+)$/);
  if (m) { var mul = getMult(m[2]); return mul != null ? parseFloat(m[1]+'.'+m[3]) * mul : null; }
  m = str.match(/^(\d+\.?\d*)\s*([pnumkMGR\u00b5\u03bc])$/);
  if (m) { var mul = getMult(m[2]); return mul != null ? parseFloat(m[1]) * mul : null; }
  return null;
}

function extractValueFromDesc(desc) {
  if (!desc) return null;
  var m = desc.match(/(\d+\.?\d*)\s*([pnumkMG\u00b5\u03bc])?\s*(F|\u03a9|\u2126|H)/i);
  if (!m) return null;
  var num = parseFloat(m[1]);
  var mul = m[2] ? (getMult(m[2]) || 1) : 1;
  return num * mul;
}

function componentTypeFromRefs(refs) {
  if (!refs) return null;
  var ch = refs.trim().charAt(0).toUpperCase();
  return 'CRL'.includes(ch) ? ch : null;
}

function componentTypeFromSection(section) {
  if (!section) return null;
  if (/Capacitor/i.test(section)) return 'C';
  if (/Resistor/i.test(section)) return 'R';
  if (/Inductor/i.test(section)) return 'L';
  return null;
}

// ── Normalize float to stable string key (avoids IEEE 754 mismatch) ──

function valueKey(type, val) {
  if (val === 0) return type + ":0";
  return type + ":" + val.toPrecision(6);
}

// ── Build lookup maps from inventory ──

function buildLookupMaps(inventory) {
  const invByLCSC = {};
  const invByMPN = {};
  const invByValue = {};

  inventory.forEach(item => {
    if (item.lcsc) invByLCSC[item.lcsc.toUpperCase()] = item;
    if (item.mpn) invByMPN[item.mpn.toUpperCase()] = item;

    var type = componentTypeFromSection(item.section);
    if (!type) return;
    var val = extractValueFromDesc(item.description);
    if (val == null) return;
    var key = valueKey(type, val);
    if (!invByValue[key]) invByValue[key] = [];
    invByValue[key].push(item);
  });

  return { invByLCSC, invByMPN, invByValue };
}

// ── Find value match (possible match) ──

function findValueMatch(bom, inventory, invByValue) {
  var bomVal = parseEEValue(bom.value);
  if (bomVal == null) bomVal = extractValueFromDesc(bom.value);
  if (bomVal == null) bomVal = parseEEValue(bom.desc);
  if (bomVal == null) bomVal = extractValueFromDesc(bom.desc);
  if (bomVal == null) return null;

  var bomType = componentTypeFromRefs(bom.refs);
  var best = null, bestQty = -1;

  inventory.forEach(function(item) {
    if (bomType) {
      var invType = componentTypeFromSection(item.section);
      if (invType && invType !== bomType) return;
    }
    var invVal = extractValueFromDesc(item.description);
    if (invVal == null) return;
    if (bomVal === 0 && invVal === 0) { /* match */ }
    else if (bomVal === 0 || invVal === 0) return;
    else if (Math.abs(bomVal - invVal) / Math.max(Math.abs(bomVal), Math.abs(invVal)) > 1e-3) return;
    if (item.qty > bestQty) { best = item; bestQty = item.qty; }
  });

  return best;
}

// ── Find alternatives (same type + value, different part) ──

function findAlternatives(bom, primaryInv, invByValue) {
  if (!primaryInv) return [];
  var bomType = componentTypeFromRefs(bom.refs);
  if (!bomType) bomType = componentTypeFromSection(primaryInv.section);
  if (!bomType) return [];
  var val = extractValueFromDesc(primaryInv.description);
  if (val == null) return [];
  var key = valueKey(bomType, val);
  var candidates = invByValue[key] || [];
  return candidates.filter(function(c) { return c !== primaryInv; });
}

// ── 5-step BOM matching ──

function matchBOM(aggregated, inventory) {
  const maps = buildLookupMaps(inventory);
  const { invByLCSC, invByMPN, invByValue } = maps;
  const results = [];

  aggregated.forEach((bom, key) => {
    let inv = null;
    let matchType = "none";

    // 1. LCSC exact match
    if (bom.lcsc && invByLCSC[bom.lcsc]) {
      inv = invByLCSC[bom.lcsc];
      matchType = "lcsc";
    }
    // 2. MPN exact match (with _ <-> . normalization)
    if (!inv && bom.mpn) {
      const mpnUpper = bom.mpn.toUpperCase();
      if (invByMPN[mpnUpper]) {
        inv = invByMPN[mpnUpper];
        matchType = "mpn";
      } else {
        const alt = mpnUpper.indexOf('_') !== -1
          ? mpnUpper.replace(/_/g, '.')
          : mpnUpper.replace(/\./g, '_');
        if (invByMPN[alt]) {
          inv = invByMPN[alt];
          matchType = "mpn";
        }
      }
    }
    // 3. MPN prefix match (one starts with the other, min 6 chars)
    if (!inv && bom.mpn && bom.mpn.length >= 6) {
      const mpnUpper = bom.mpn.toUpperCase();
      for (const [k, item] of Object.entries(invByMPN)) {
        if ((k.startsWith(mpnUpper) || mpnUpper.startsWith(k)) && Math.min(mpnUpper.length, k.length) >= 6) {
          inv = item; matchType = "mpn"; break;
        }
      }
    }
    // 4. Fuzzy MPN match (longest common prefix >= 8 chars AND >= 70% of shorter)
    if (!inv && bom.mpn && bom.mpn.length >= 8) {
      const mpnUpper = bom.mpn.toUpperCase();
      let bestItem = null, bestLen = 0;
      for (const [k, item] of Object.entries(invByMPN)) {
        let i = 0;
        while (i < mpnUpper.length && i < k.length && mpnUpper[i] === k[i]) i++;
        const shorter = Math.min(mpnUpper.length, k.length);
        if (i >= 8 && i / shorter >= 0.7 && i > bestLen) { bestItem = item; bestLen = i; }
      }
      if (bestItem) { inv = bestItem; matchType = "fuzzy"; }
    }
    // 5. Value match (possible match)
    if (!inv) {
      inv = findValueMatch(bom, inventory, invByValue);
      if (inv) matchType = "value";
    }

    let status;
    if (!inv) {
      status = "missing";
    } else if (matchType === "value" || matchType === "fuzzy") {
      status = "possible";
    } else if (bom.qty <= inv.qty) {
      status = "ok";
    } else {
      status = "short";
    }

    let alts = findAlternatives(bom, inv, invByValue);

    results.push({ bom, inv, status, matchType, alts });
  });

  // Sort: missing first, then possible, then short, then ok
  const order = { missing: 0, possible: 1, short: 2, ok: 3 };
  results.sort((a, b) => order[a.status] - order[b.status]);
  return results;
}
