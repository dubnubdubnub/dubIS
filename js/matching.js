// @ts-check
/* matching.js — 5-step BOM matching engine (ported from build_compare.py) */

import { bomKey } from './part-keys.js';

// ── Constants ──
export const VALUE_TOLERANCE = 1e-3;          // 0.1% relative error
export const MPN_PREFIX_MIN_LEN = 6;
export const MPN_FUZZY_MIN_LEN = 8;
export const MPN_FUZZY_MIN_RATIO = 0.7;      // 70% of shorter string

// ── Value parsing for possible matches ──

export function getMult(c) {
  const m = {p:1e-12, n:1e-9, u:1e-6, U:1e-6, m:1e-3, R:1, k:1e3, K:1e3, M:1e6, G:1e9};
  // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
  if (m[c] != null) return m[c];
  if (c === '\u00b5' || c === '\u03bc') return 1e-6;
  return null;
}

export function parseEEValue(str) {
  if (!str) return null;
  str = str.split(/[\/\u00b1%]/)[0].trim();
  str = str.replace(/[FH\u03a9\u2126]+$/i, '').replace(/ohm$/i, '').trim();
  let m = str.match(/^(\d+\.?\d*)([pnumkMGR\u00b5\u03bc])(\d+)$/);
  // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
  if (m) { const mul = getMult(m[2]); return mul != null ? parseFloat(m[1]+'.'+m[3]) * mul : null; }
  m = str.match(/^(\d+\.?\d*)\s*([pnumkMGR\u00b5\u03bc])$/);
  // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
  if (m) { const mul = getMult(m[2]); return mul != null ? parseFloat(m[1]) * mul : null; }
  return null;
}

export function extractValueFromDesc(desc) {
  if (!desc) return null;
  const m = desc.match(/(\d+\.?\d*)\s*([pnumkMG\u00b5\u03bc])?\s*(F|\u03a9|\u2126|H)/i);
  if (!m) return null;
  const num = parseFloat(m[1]);
  const mul = m[2] ? (getMult(m[2]) || 1) : 1;
  return num * mul;
}

// ── Extract BOM value (tries value then desc, both parseEE and extractFromDesc) ──

export function extractBomValue(bom) {
  let val = parseEEValue(bom.value);
  // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
  if (val == null) val = extractValueFromDesc(bom.value);
  // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
  if (val == null) val = parseEEValue(bom.desc);
  // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
  if (val == null) val = extractValueFromDesc(bom.desc);
  return val;
}

export function componentTypeFromRefs(refs) {
  if (!refs) return null;
  const ch = refs.trim().charAt(0).toUpperCase();
  return 'CRL'.includes(ch) ? ch : null;
}

export function componentTypeFromSection(section) {
  if (!section) return null;
  if (/Capacitor/i.test(section)) return 'C';
  if (/Resistor/i.test(section)) return 'R';
  if (/Inductor/i.test(section)) return 'L';
  return null;
}

// ── Validate fuzzy/prefix matches for passive components ──
// Priority: package, value, name — reject if package or value mismatches.

export function isPassiveSection(section) {
  return /Resistor|Capacitor|Inductor/i.test(section || "");
}

// ── Canonical footprint extraction ──
// Whitelist of known package codes. Word-boundary anchored so MPN-like strings
// (e.g. "0603WAF0000T5E") do not match "0603". Only ever called on bom.footprint
// and invItem.package — never on MPN or description fields.
const FOOTPRINT_WHITELIST = [
  // Chip passives
  '0201', '0402', '0603', '0805', '1206', '1210', '1812', '2010', '2512',
  // SOT family
  'SOT-23-5', 'SOT-23-6', 'SOT-23', 'SOT-323', 'SOT-363', 'SOT-89', 'SOT-223',
  // SOIC / MSOP / VSSOP / TSSOP / SSOP
  'SOIC-8', 'SOIC-14', 'SOIC-16',
  'MSOP-8', 'MSOP-10',
  'VSSOP-8', 'VSSOP-10',
  'TSSOP-8', 'TSSOP-14', 'TSSOP-16', 'TSSOP-20',
  // Diode packages
  'SOD-123', 'SOD-323', 'SOD-523',
  'DO-214AA', 'DO-214AC', 'DO-214',
  // Leadless / fine-pitch (with numeric suffix)
  'QFN-16', 'QFN-20', 'QFN-24', 'QFN-32', 'QFN-48', 'QFN-64',
  'DFN-6', 'DFN-8', 'DFN-10',
  'LQFP-32', 'LQFP-48', 'LQFP-64', 'LQFP-100', 'LQFP-144',
  'TQFP-32', 'TQFP-48', 'TQFP-64', 'TQFP-100',
];

// Pre-compiled regex: matches any whitelist entry at a word boundary, longest first.
const FOOTPRINT_REGEX = new RegExp(
  '(?:^|[^A-Za-z0-9])(' +
    [...FOOTPRINT_WHITELIST].sort((a, b) => b.length - a.length)
      .map(c => c.replace(/-/g, '\\-'))
      .join('|') +
  ')(?=[^A-Za-z0-9]|$)',
  'i'
);

export function extractFootprintCode(str) {
  if (!str) return null;
  const m = String(str).match(FOOTPRINT_REGEX);
  if (!m) return null;
  const raw = m[1].toUpperCase();
  // Normalize to the canonical whitelist spelling (preserves exact hyphenation).
  const canonical = FOOTPRINT_WHITELIST.find(c => c.toUpperCase() === raw);
  return canonical || raw;
}

export function footprintsCompatible(bom, invItem) {
  const bomCode = extractFootprintCode(bom.footprint);
  const invCode = extractFootprintCode(invItem.package);
  if (!bomCode || !invCode) return true;
  return bomCode === invCode;
}

// packagesCompatible — legacy substring-based check, retained for export compatibility.
// New code should use footprintsCompatible, which uses canonical codes.
export function packagesCompatible(bom, invItem) {
  const bomPkg = (bom.footprint || "").toUpperCase();
  const invPkg = (invItem.package || "").toUpperCase();
  if (!bomPkg || !invPkg) return true;
  return bomPkg.includes(invPkg) || invPkg.includes(bomPkg);
}

export function valuesCompatible(bom, invItem) {
  const bomVal = extractBomValue(bom);
  const invVal = extractValueFromDesc(invItem.description);
  // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
  if (bomVal == null || invVal == null) return true;
  if (bomVal === 0 && invVal === 0) return true;
  if (bomVal === 0 || invVal === 0) return false;
  return Math.abs(bomVal - invVal) / Math.max(Math.abs(bomVal), Math.abs(invVal)) <= VALUE_TOLERANCE;
}

export function isFuzzyMatchValid(bom, invItem) {
  if (!isPassiveSection(invItem.section)) return true;
  return footprintsCompatible(bom, invItem) && valuesCompatible(bom, invItem);
}

// ── Normalize float to stable string key (avoids IEEE 754 mismatch) ──

export function valueKey(type, val) {
  if (val === 0) return type + ":0";
  return type + ":" + val.toPrecision(6);
}

// ── Build lookup maps from inventory ──

export function buildLookupMaps(inventory) {
  const invByLCSC = {};
  const invByMPN = {};
  const invByValue = {};

  inventory.forEach(item => {
    if (item.lcsc) invByLCSC[item.lcsc.toUpperCase()] = item;
    if (item.mpn) invByMPN[item.mpn.toUpperCase()] = item;

    const type = componentTypeFromSection(item.section);
    if (!type) return;
    const val = extractValueFromDesc(item.description);
    // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
    if (val == null) return;
    const key = valueKey(type, val);
    if (!invByValue[key]) invByValue[key] = [];
    invByValue[key].push(item);
  });

  return { invByLCSC, invByMPN, invByValue };
}

// ── Find value match (possible match) ──

export function findValueMatch(bom, inventory, invByValue) {
  const bomVal = extractBomValue(bom);
  // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
  if (bomVal == null) return null;

  const bomType = componentTypeFromRefs(bom.refs);

  // O(1) lookup when component type is known
  if (bomType) {
    const key = valueKey(bomType, bomVal);
    const candidates = invByValue[key] || [];
    let best = null, bestQty = -1;
    for (let i = 0; i < candidates.length; i++) {
      if (!footprintsCompatible(bom, candidates[i])) continue;
      if (candidates[i].qty > bestQty) { best = candidates[i]; bestQty = candidates[i].qty; }
    }
    return best;
  }

  // Fallback: scan all value groups when type is unknown
  let best = null, bestQty = -1;
  for (const key in invByValue) {
    const candidates = invByValue[key];
    for (let i = 0; i < candidates.length; i++) {
      const item = candidates[i];
      const invVal = extractValueFromDesc(item.description);
      // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
      if (invVal == null) continue;
      if (bomVal === 0 && invVal === 0) { /* match */ }
      else if (bomVal === 0 || invVal === 0) continue;
      else if (Math.abs(bomVal - invVal) / Math.max(Math.abs(bomVal), Math.abs(invVal)) > VALUE_TOLERANCE) continue;
      if (!footprintsCompatible(bom, item)) continue;
      if (item.qty > bestQty) { best = item; bestQty = item.qty; }
    }
  }
  return best;
}

// ── Find alternatives (same type + value, different part) ──

export function findAlternatives(bom, primaryInv, invByValue) {
  if (!primaryInv) return [];
  let bomType = componentTypeFromRefs(bom.refs);
  if (!bomType) bomType = componentTypeFromSection(primaryInv.section);
  if (!bomType) return [];
  const val = extractValueFromDesc(primaryInv.description);
  // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
  if (val == null) return [];
  const key = valueKey(bomType, val);
  const candidates = invByValue[key] || [];
  return candidates.filter(function(c) { return c !== primaryInv; });
}

// ── 5-step BOM matching ──

export function matchBOM(aggregated, inventory, manualLinks, confirmedMatches, genericParts) {
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
    let genericPartId = null;
    let genericPartName = null;
    let genericMembers = null;
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
    // 5.5. Generic part resolution (before value match — more specific than a raw value match)
    if (!inv && genericParts && genericParts.length > 0) {
      const bomVal = extractBomValue(bom);
      const bomType = componentTypeFromRefs(bom.refs);
      const bomPkg = (bom.footprint || "").toUpperCase();

      for (const gp of genericParts) {
        // Check type compatibility
        const gpType = gp.part_type === "capacitor" ? "C"
                     : gp.part_type === "resistor" ? "R"
                     : gp.part_type === "inductor" ? "L" : null;
        if (bomType && gpType && bomType !== gpType) continue;

        // Check value from spec
        const specVal = parseEEValue(gp.spec.value) ?? extractValueFromDesc(gp.spec.value);
        // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
        if (specVal == null || bomVal == null) continue;
        if (specVal !== 0 && bomVal !== 0) {
          if (Math.abs(specVal - bomVal) / Math.max(Math.abs(specVal), Math.abs(bomVal)) > VALUE_TOLERANCE) continue;
        } else if (specVal !== bomVal) continue;

        // Check package from spec
        const gpPkg = (gp.spec.package || "").toUpperCase();
        if (bomPkg && gpPkg && !bomPkg.includes(gpPkg) && !gpPkg.includes(bomPkg)) continue;

        // Match found — resolve to best member
        if (gp.members && gp.members.length > 0) {
          // Sort: preferred first, then by quantity descending
          const sorted = [...gp.members].sort((a, b) => {
            if (a.preferred !== b.preferred) return b.preferred - a.preferred;
            return b.quantity - a.quantity;
          });
          const bestId = sorted[0].part_id;
          const found = invByLCSC[bestId.toUpperCase()] || invByMPN[bestId.toUpperCase()];
          if (found) {
            inv = found;
            matchType = "generic";
            genericPartId = gp.generic_part_id;
            genericPartName = gp.name;
            genericMembers = gp.members;
            break;
          }
        }
      }
    }

    // 5. Value match (possible match — fallback when no generic part matched)
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

    results.push({ bom, inv, status, matchType, alts, genericPartId: genericPartId || null, genericPartName: genericPartName || null, genericMembers: genericMembers || null });
  });

  return results;
}
