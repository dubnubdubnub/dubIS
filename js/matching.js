/* matching.js — 5-step BOM matching engine (ported from build_compare.py) */

// ── Constants ──
const VALUE_TOLERANCE = 1e-3;          // 0.1% relative error
const MPN_PREFIX_MIN_LEN = 6;
const MPN_FUZZY_MIN_LEN = 8;
const MPN_FUZZY_MIN_RATIO = 0.7;      // 70% of shorter string

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
  let m = str.match(/^(\d+\.?\d*)([pnumkMGR\u00b5\u03bc])(\d+)$/);
  if (m) { const mul = getMult(m[2]); return mul != null ? parseFloat(m[1]+'.'+m[3]) * mul : null; }
  m = str.match(/^(\d+\.?\d*)\s*([pnumkMGR\u00b5\u03bc])$/);
  if (m) { const mul = getMult(m[2]); return mul != null ? parseFloat(m[1]) * mul : null; }
  return null;
}

function extractValueFromDesc(desc) {
  if (!desc) return null;
  const m = desc.match(/(\d+\.?\d*)\s*([pnumkMG\u00b5\u03bc])?\s*(F|\u03a9|\u2126|H)/i);
  if (!m) return null;
  const num = parseFloat(m[1]);
  const mul = m[2] ? (getMult(m[2]) || 1) : 1;
  return num * mul;
}

// ── Extract BOM value (tries value then desc, both parseEE and extractFromDesc) ──

function extractBomValue(bom) {
  let val = parseEEValue(bom.value);
  if (val == null) val = extractValueFromDesc(bom.value);
  if (val == null) val = parseEEValue(bom.desc);
  if (val == null) val = extractValueFromDesc(bom.desc);
  return val;
}

function componentTypeFromRefs(refs) {
  if (!refs) return null;
  const ch = refs.trim().charAt(0).toUpperCase();
  return 'CRL'.includes(ch) ? ch : null;
}

function componentTypeFromSection(section) {
  if (!section) return null;
  if (/Capacitor/i.test(section)) return 'C';
  if (/Resistor/i.test(section)) return 'R';
  if (/Inductor/i.test(section)) return 'L';
  return null;
}

// ── Validate fuzzy/prefix matches for passive components ──
// Priority: package, value, name — reject if package or value mismatches.

function isPassiveSection(section) {
  return /Resistor|Capacitor|Inductor/i.test(section || "");
}

function packagesCompatible(bom, invItem) {
  const bomPkg = (bom.footprint || "").toUpperCase();
  const invPkg = (invItem.package || "").toUpperCase();
  if (!bomPkg || !invPkg) return true;
  return bomPkg.includes(invPkg) || invPkg.includes(bomPkg);
}

function valuesCompatible(bom, invItem) {
  const bomVal = extractBomValue(bom);
  const invVal = extractValueFromDesc(invItem.description);
  if (bomVal == null || invVal == null) return true;
  if (bomVal === 0 && invVal === 0) return true;
  if (bomVal === 0 || invVal === 0) return false;
  return Math.abs(bomVal - invVal) / Math.max(Math.abs(bomVal), Math.abs(invVal)) <= VALUE_TOLERANCE;
}

function isFuzzyMatchValid(bom, invItem) {
  if (!isPassiveSection(invItem.section)) return true;
  return packagesCompatible(bom, invItem) && valuesCompatible(bom, invItem);
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

    const type = componentTypeFromSection(item.section);
    if (!type) return;
    const val = extractValueFromDesc(item.description);
    if (val == null) return;
    const key = valueKey(type, val);
    if (!invByValue[key]) invByValue[key] = [];
    invByValue[key].push(item);
  });

  return { invByLCSC, invByMPN, invByValue };
}

// ── Find value match (possible match) ──

function findValueMatch(bom, inventory, invByValue) {
  const bomVal = extractBomValue(bom);
  if (bomVal == null) return null;

  const bomType = componentTypeFromRefs(bom.refs);
  let best = null, bestQty = -1;

  inventory.forEach(function(item) {
    if (bomType) {
      const invType = componentTypeFromSection(item.section);
      if (invType && invType !== bomType) return;
    }
    const invVal = extractValueFromDesc(item.description);
    if (invVal == null) return;
    if (bomVal === 0 && invVal === 0) { /* match */ }
    else if (bomVal === 0 || invVal === 0) return;
    else if (Math.abs(bomVal - invVal) / Math.max(Math.abs(bomVal), Math.abs(invVal)) > VALUE_TOLERANCE) return;
    if (item.qty > bestQty) { best = item; bestQty = item.qty; }
  });

  return best;
}

// ── Find alternatives (same type + value, different part) ──

function findAlternatives(bom, primaryInv, invByValue) {
  if (!primaryInv) return [];
  let bomType = componentTypeFromRefs(bom.refs);
  if (!bomType) bomType = componentTypeFromSection(primaryInv.section);
  if (!bomType) return [];
  const val = extractValueFromDesc(primaryInv.description);
  if (val == null) return [];
  const key = valueKey(bomType, val);
  const candidates = invByValue[key] || [];
  return candidates.filter(function(c) { return c !== primaryInv; });
}

// ── 5-step BOM matching ──

function matchBOM(aggregated, inventory, manualLinks, confirmedMatches) {
  const maps = buildLookupMaps(inventory);
  const { invByLCSC, invByMPN, invByValue } = maps;
  const results = [];

  // Build manual link lookup: bomKey -> invPartKey
  const manualLinkMap = {};
  if (manualLinks && manualLinks.length > 0) {
    manualLinks.forEach(link => { manualLinkMap[link.bomKey] = link.invPartKey; });
  }

  // Build confirmed match lookup: bomKey -> invPartKey
  const confirmedMap = {};
  if (confirmedMatches && confirmedMatches.length > 0) {
    confirmedMatches.forEach(link => { confirmedMap[link.bomKey] = link.invPartKey; });
  }

  aggregated.forEach((bom, key) => {
    let inv = null;
    let matchType = "none";
    const bk = bomKey(bom);

    // 0. Manual link override (use clean bomKey, not the :DNP-suffixed map key)
    if (manualLinkMap[bk]) {
      const invKey = manualLinkMap[bk];
      const found = invByLCSC[invKey.toUpperCase()] || invByMPN[invKey.toUpperCase()];
      if (found) {
        inv = found;
        matchType = "manual";
      }
    }

    // 0.5. Confirmed match override (use clean bomKey)
    if (!inv && confirmedMap[bk]) {
      const invKey = confirmedMap[bk];
      const found = invByLCSC[invKey.toUpperCase()] || invByMPN[invKey.toUpperCase()];
      if (found) { inv = found; matchType = "confirmed"; }
    }

    // 1. LCSC exact match
    if (!inv && bom.lcsc && invByLCSC[bom.lcsc]) {
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
    if (!inv && bom.mpn && bom.mpn.length >= MPN_PREFIX_MIN_LEN) {
      const mpnUpper = bom.mpn.toUpperCase();
      for (const [k, item] of Object.entries(invByMPN)) {
        if ((k.startsWith(mpnUpper) || mpnUpper.startsWith(k)) && Math.min(mpnUpper.length, k.length) >= MPN_PREFIX_MIN_LEN) {
          if (isFuzzyMatchValid(bom, item)) { inv = item; matchType = "mpn"; break; }
        }
      }
    }
    // 4. Fuzzy MPN match (longest common prefix >= 8 chars AND >= 70% of shorter)
    if (!inv && bom.mpn && bom.mpn.length >= MPN_FUZZY_MIN_LEN) {
      const mpnUpper = bom.mpn.toUpperCase();
      let bestItem = null, bestLen = 0;
      for (const [k, item] of Object.entries(invByMPN)) {
        let i = 0;
        while (i < mpnUpper.length && i < k.length && mpnUpper[i] === k[i]) i++;
        const shorter = Math.min(mpnUpper.length, k.length);
        if (i >= MPN_FUZZY_MIN_LEN && i / shorter >= MPN_FUZZY_MIN_RATIO && i > bestLen && isFuzzyMatchValid(bom, item)) { bestItem = item; bestLen = i; }
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

    const alts = findAlternatives(bom, inv, invByValue);

    results.push({ bom, inv, status, matchType, alts });
  });

  return results;
}
