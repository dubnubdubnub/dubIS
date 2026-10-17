// @ts-check
/* matching.js — 5-step BOM matching engine (ported from build_compare.py) */

import { bomKey } from './part-keys.js';

// ── Constants ──
export const VALUE_TOLERANCE = 1e-3;          // 0.1% relative error
export const MPN_PREFIX_MIN_LEN = 6;
export const MPN_FUZZY_MIN_LEN = 8;
export const MPN_FUZZY_MIN_RATIO = 0.7;      // 70% of shorter string

/* ── The evidence rule ──
   An identity match (LCSC code, exact MPN, a manual link, a confirmed match)
   is a fact about the part. Everything else this module proposes — an MPN
   prefix or fuzzy hit, a value match, a generic-group resolution, an alternate —
   is a SUGGESTION, and for a passive a suggestion is only as good as the
   electrical facts behind it. So, for passives:

     - the value must be KNOWN on both sides and equal. Unknown is never a pass
       (the same rule domain/predicates.py applies server-side). A shared MPN
       prefix is no evidence at all for passive families, which encode the value
       mid-string: 0402WGF1001TCE (1kΩ) and 0402WGF1000TCE (100Ω) share ten
       characters and nothing else that matters.
     - the footprint must not be known-different.
     - a capacitor whose voltage rating is known must not be offered a part
       rated LOWER. An inventory part with no stated voltage is allowed through
       on purpose: every non-identity match lands as "possible" and needs a human
       to confirm it, hand-entered inventory descriptions routinely omit the
       rating, and hiding the only same-value candidate would turn "check this"
       into a silent "missing". A known-lower rating, by contrast, is a proven
       wrong answer and is never shown.

   A BOM row's facts come from its own Value/Description/Footprint columns
   first, then from `bom.spec` — what the distributor says about the row's LCSC
   code (js/bom/bom-spec-lookup.js). The spec is consulted, never written back:
   the user's columns are what Save BOM writes. */

// ── Value parsing for possible matches ──

export function getMult(c) {
  // N/P are here for all-caps distributor text ("CAP CER 100NF"): without them
  // the multiplier silently fell back to 1 and 100NF read as 100 farads. The
  // strict parseEEValue grammar is case-sensitive and never feeds them in.
  const m = {p:1e-12, P:1e-12, n:1e-9, N:1e-9, u:1e-6, U:1e-6, m:1e-3, R:1, k:1e3, K:1e3, M:1e6, G:1e9};
  // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
  if (m[c] != null) return m[c];
  if (c === 'µ' || c === 'μ') return 1e-6;
  return null;
}

export function parseEEValue(str) {
  if (!str) return null;
  str = str.split(/[\/±%]/)[0].trim();
  str = str.replace(/[FHΩΩ]+$/i, '').replace(/\s*ohms?$/i, '').trim();
  let m = str.match(/^(\d+\.?\d*)([pnumkMGRµμ])(\d+)$/);
  // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
  if (m) { const mul = getMult(m[2]); return mul != null ? parseFloat(m[1]+'.'+m[3]) * mul : null; }
  m = str.match(/^(\d+\.?\d*)\s*([pnumkMGRµμ])$/);
  // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
  if (m) { const mul = getMult(m[2]); return mul != null ? parseFloat(m[1]) * mul : null; }
  return null;
}

/** Which passive a unit names: F → C, H → L, Ω/OHM → R. */
function typeOfUnit(unit) {
  if (/^F$/i.test(unit)) return 'C';
  if (/^H$/i.test(unit)) return 'L';
  return 'R';
}

/** parseEEValue, refusing a value whose own unit names a different passive. */
function parseEEValueTyped(str, type) {
  if (!str) return null;
  if (type) {
    const head = String(str).split(/[\/±%]/)[0].trim();
    const unit = head.match(/(ohms?|F|H|Ω|Ω)$/i);
    if (unit && typeOfUnit(unit[1]) !== type) return null;
  }
  return parseEEValue(str);
}

// Unit group accepts the symbols (F, Ω, H) and the spelled-out "OHM"/"OHMS"
// word form distributors use in all-caps descriptions ("RES SMD 330 OHM 0.5%").
// The trailing lookahead keeps "OHM" from matching inside a longer word.
// The multiplier keeps its case even under /i: getMult maps "M" to mega and
// "m" to milli, so "1 MOHM" is 1e6 and "10 mOhm" is 1e-2.
const VALUE_IN_TEXT = /(\d+\.?\d*)\s*([pnumkMGµμ])?\s*(OHMS?(?![A-Za-z])|F|Ω|Ω|H)/gi;

/**
 * First electrical value in free text.
 * `type` (C/R/L), when known, skips values whose unit names another passive —
 * a capacitor line's "120mΩ ESR" is not its capacitance.
 * `bounded` demands the value stand alone as a token, which is what a BOM
 * Value cell needs: KiCad exports routinely put the MPN there, and an MPN's
 * digit runs read as values — GRM155R71H104KE14D contains "71H" (71 henries),
 * RC0402FR-071KL contains "0402F" (402 farads).
 * @param {string|null|undefined} text
 * @param {string|null} [type]
 * @param {boolean} [bounded]
 * @returns {number|null}
 */
export function valueFromText(text, type = null, bounded = false) {
  if (!text) return null;
  const s = String(text);
  for (const m of s.matchAll(VALUE_IN_TEXT)) {
    if (bounded) {
      const idx = /** @type {number} */ (m.index);
      const before = idx > 0 ? s[idx - 1] : '';
      const after = s[idx + m[0].length] || '';
      if (/[A-Za-z0-9.]/.test(before) || /[A-Za-z0-9]/.test(after)) continue;
    }
    if (type && typeOfUnit(m[3]) !== type) continue;
    const mul = m[2] ? (getMult(m[2]) || 1) : 1;
    return parseFloat(m[1]) * mul;
  }
  return null;
}

export function extractValueFromDesc(desc) {
  return valueFromText(desc, null, false);
}

/** Relative-tolerance equality, with 0 equal only to 0. */
export function valuesEqual(a, b) {
  if (a === 0 && b === 0) return true;
  if (a === 0 || b === 0) return false;
  return Math.abs(a - b) / Math.max(Math.abs(a), Math.abs(b)) <= VALUE_TOLERANCE;
}

// ── Distributor spec (bom.spec) accessors ──

/**
 * Value of the first spec attribute whose name is one of `names`.
 * @param {any} spec
 * @param {string[]} names
 * @returns {string|null}
 */
export function specAttribute(spec, names) {
  if (!spec || !Array.isArray(spec.attributes)) return null;
  const wanted = names.map(n => n.toLowerCase());
  for (const a of spec.attributes) {
    if (a && typeof a.name === 'string' && wanted.includes(a.name.toLowerCase())) {
      return typeof a.value === 'string' ? a.value : null;
    }
  }
  return null;
}

const SPEC_VALUE_ATTRS = { C: ['Capacitance'], R: ['Resistance'], L: ['Inductance'] };
const SPEC_VOLTAGE_ATTRS = ['Voltage Rating', 'Voltage - Rated', 'Rated Voltage'];

function specValue(spec, type) {
  if (!spec) return null;
  for (const t of (type ? [type] : ['C', 'R', 'L'])) {
    const attr = specAttribute(spec, SPEC_VALUE_ATTRS[t]);
    // valueFromText as well as the strict grammar: parseEEValue insists on a
    // multiplier, so LCSC's plain "100Ω" Resistance read as unknown — found by
    // the metamorphic property in tests/js/property/matching.property.test.mjs,
    // pinned in tests/js/matching-spec.test.js.
    const v = parseEEValueTyped(attr, t) ?? valueFromText(attr, t, false);
    // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
    if (v != null) return v;
  }
  return valueFromText(spec.description, type, false);
}

/**
 * C/R/L from a distributor spec: its category ("Capacitors", "Chip Resistor -
 * Surface Mount"), else which value attribute it carries — a spec with a
 * Capacitance is a capacitor's, whatever the category is called.
 */
export function componentTypeFromSpec(spec) {
  if (!spec) return null;
  const byCategory = componentTypeFromSection([spec.category, spec.subcategory].filter(Boolean).join(' '));
  if (byCategory) return byCategory;
  for (const t of /** @type {const} */ (['C', 'R', 'L'])) {
    if (specAttribute(spec, SPEC_VALUE_ATTRS[t])) return t;
  }
  return null;
}

/** C/R/L from a KiCad standard-library footprint ("Capacitor_SMD:C_0402_1005Metric"). */
export function componentTypeFromFootprintLib(footprint) {
  const m = String(footprint || '').match(/^(Capacitor|Resistor|Inductor)_(?:SMD|THT)\b/);
  if (!m) return null;
  return m[1] === 'Capacitor' ? 'C' : m[1] === 'Resistor' ? 'R' : 'L';
}

/** C/R/L from the unit of the row's own value text ("100nF" says capacitor). */
function componentTypeFromValueUnit(bom) {
  for (const [text, bounded] of [[bom.value, true], [bom.desc, false]]) {
    if (!text) continue;
    const s = String(text);
    const strict = s.split(/[\/\u00b1%]/)[0].trim().match(/^\d+\.?\d*[pnumkMGR\u00b5\u03bc]?\d*\s*(F|H|\u03a9|\u2126|ohms?)$/i);
    if (strict) return typeOfUnit(strict[1]);
    for (const m of s.matchAll(VALUE_IN_TEXT)) {
      if (bounded) {
        const idx = /** @type {number} */ (m.index);
        if (/[A-Za-z0-9.]/.test(idx > 0 ? s[idx - 1] : '') || /[A-Za-z0-9]/.test(s[idx + m[0].length] || '')) continue;
      }
      return typeOfUnit(m[3]);
    }
  }
  return null;
}

/**
 * The BOM row's passive type, or null when it is not (known to be) one.
 * A designator, when present, is authoritative both ways: "C3" is a capacitor
 * and "U1" is NOT a passive, whatever its description mentions ("LDO, 1uF
 * output cap" must not turn a regulator line into a capacitor). Only a row
 * with no designator falls back to its footprint library, the distributor's
 * category, and finally the unit its own value is written in.
 */
export function bomComponentType(bom) {
  if (String(bom.refs || '').trim()) return componentTypeFromRefs(bom.refs);
  return componentTypeFromFootprintLib(bom.footprint)
    || componentTypeFromSpec(bom.spec)
    || componentTypeFromValueUnit(bom);
}

// ── Extract BOM value (own columns first, then the distributor spec) ──

// Plain-number fallback specifically for resistors — only applied when the BOM row's
// type is resistor. Capacitors/inductors remain strict (plain numbers are
// ambiguous for those).
function parseResistorPlainNumber(str) {
  if (!str) return null;
  const trimmed = String(str).trim();
  if (!/^\d+(\.\d+)?$/.test(trimmed)) return null;
  const n = parseFloat(trimmed);
  return Number.isFinite(n) ? n : null;
}

/**
 * The BOM row's electrical value, or null when it cannot be known.
 * `typeHint` is the type to assume when the row itself names none (a caller
 * comparing against an inventory part passes that part's section type).
 * @param {object} bom
 * @param {string|null} [typeHint]
 * @returns {number|null}
 */
export function extractBomValue(bom, typeHint = null) {
  const type = bomComponentType(bom) || typeHint || null;
  const own = parseEEValueTyped(bom.value, type)
    ?? valueFromText(bom.value, type, true)
    ?? parseEEValueTyped(bom.desc, type)
    ?? valueFromText(bom.desc, type, false)
    ?? (type === 'R' ? parseResistorPlainNumber(bom.value) : null);
  // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
  if (own != null) return own;
  return specValue(bom.spec, type);
}

// ── Voltage rating ──

// "50V", "6.3V", "1kV", "25VDC", and the "6V3" designator style. The leading
// boundary keeps an MPN's digit run ("…16V…" inside a part number) from
// reading as a rating; "mV" never matches because only k is a multiplier here.
const VOLTAGE_IN_TEXT = /(?:^|[^A-Za-z0-9.])(?:(\d+)V(\d+)|(\d+(?:\.\d+)?)\s*(k)?V(?:DC)?)(?![A-Za-z0-9])/i;

/**
 * @param {string|null|undefined} text
 * @returns {number|null} volts
 */
export function extractVoltage(text) {
  if (!text) return null;
  const m = String(text).match(VOLTAGE_IN_TEXT);
  if (!m) return null;
  if (m[1] !== undefined) return parseFloat(m[1] + '.' + m[2]);
  return parseFloat(m[3]) * (m[4] ? 1e3 : 1);
}

/** The BOM row's voltage rating: own columns first, then the distributor spec. */
export function extractBomVoltage(bom) {
  const own = extractVoltage(bom.value) ?? extractVoltage(bom.desc);
  // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
  if (own != null) return own;
  if (!bom.spec) return null;
  return extractVoltage(specAttribute(bom.spec, SPEC_VOLTAGE_ATTRS)) ?? extractVoltage(bom.spec.description);
}

/**
 * False only when a capacitor's rating is known on both sides and the
 * inventory part's is lower. Unknown inventory voltage passes — see the
 * evidence rule at the top of this file for why.
 * @param {object} bom
 * @param {import('./types.js').InventoryItem} invItem
 * @returns {boolean}
 */
export function voltageAdequate(bom, invItem) {
  // BOM side first, so a line with no known rating never touches invItem:
  // findAlternatives filters whole value groups with this (see the read
  // counts in tests/js/matching-perf.test.js).
  const need = extractBomVoltage(bom);
  // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
  if (need == null) return true;
  const type = bomComponentType(bom) || componentTypeFromSection(invItem.section);
  if (type !== 'C') return true;
  const have = extractVoltage(invItem.description);
  // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
  if (have == null) return true;
  return have >= need * (1 - VALUE_TOLERANCE);
}

export function componentTypeFromRefs(refs) {
  if (!refs) return null;
  // The whole alphabetic prefix, not its first letter: CN1/CON1 are connectors,
  // LED1 is a diode, RV1 a varistor — none is a C/L/R, and calling them one
  // would subject their MPN matches to the passive evidence rule.
  const m = String(refs).trim().match(/^([A-Za-z]+)/);
  if (!m) return null;
  const prefix = m[1].toUpperCase();
  return prefix === 'C' || prefix === 'R' || prefix === 'L' ? prefix : null;
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
// (e.g. "0603WAF0000T5E") do not match "0603". Safe to call on bom.footprint,
// invItem.package, and invItem.description (the word-boundary anchor handles
// the package code that distributor descriptions reliably tack on, e.g.
// "22uF ±20% 10V Ceramic Capacitor X5R 0603"). Never call on MPN strings —
// they can contain raw digit runs that collide with chip-size codes.
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

// Resolve inventory's footprint code, falling back to its description when the
// package field is empty. Some inventory rows (e.g. older LCSC imports) have an
// empty package column but reliably include the chip size in the description.
/**
 * @param {import('./types.js').InventoryItem|null} invItem
 * @returns {string|null}
 */
export function invFootprintCode(invItem) {
  if (!invItem) return null;
  return extractFootprintCode(invItem.package) || extractFootprintCode(invItem.description);
}

/**
 * The BOM row's footprint code: its own Footprint column, then the
 * distributor's package for its LCSC code. Own column first because it is the
 * land pattern on the board — the thing a substitute actually has to fit.
 * @param {object} bom
 * @returns {string|null}
 */
export function bomFootprintCode(bom) {
  const own = extractFootprintCode(bom.footprint);
  if (own || !bom.spec) return own;
  return extractFootprintCode(bom.spec.package) || extractFootprintCode(bom.spec.description);
}

/**
 * @param {object} bom
 * @param {import('./types.js').InventoryItem|null} invItem
 * @returns {boolean}
 */
export function footprintsCompatible(bom, invItem) {
  const bomCode = bomFootprintCode(bom);
  // Short-circuit before touching invItem: callers filter whole value groups
  // with this, and a BOM line with no footprint code must stay O(1) per item.
  if (!bomCode) return true;
  const invCode = invFootprintCode(invItem);
  if (!invCode) return true;
  return bomCode === invCode;
}

// packagesCompatible — legacy substring-based check, retained for export compatibility.
// New code should use footprintsCompatible, which uses canonical codes.
/**
 * @param {object} bom
 * @param {import('./types.js').InventoryItem} invItem
 * @returns {boolean}
 */
export function packagesCompatible(bom, invItem) {
  const bomPkg = (bom.footprint || "").toUpperCase();
  const invPkg = (invItem.package || "").toUpperCase();
  if (!bomPkg || !invPkg) return true;
  return bomPkg.includes(invPkg) || invPkg.includes(bomPkg);
}

/**
 * An inventory part's electrical value, read as its section's passive type.
 * The one reader buildLookupMaps, findValueMatch, findAlternatives and the
 * fuzzy check share, so they cannot disagree about what a part's value is.
 * @param {import('./types.js').InventoryItem} invItem
 * @returns {number|null}
 */
export function invValue(invItem) {
  return valueFromText(invItem.description, componentTypeFromSection(invItem.section), false);
}

/**
 * True only when both values are KNOWN and equal. Unknown is never a pass:
 * this used to answer true when either side was unparseable, which is how a
 * Value=MPN KiCad line (no parseable value) was "compatible" with every
 * resistor sharing its MPN prefix.
 * @param {object} bom
 * @param {import('./types.js').InventoryItem} invItem
 * @param {string|null} [typeHint] passive type to read the BOM row as when it names none
 * @returns {boolean}
 */
export function valuesCompatible(bom, invItem, typeHint = null) {
  const bomVal = extractBomValue(bom, typeHint || componentTypeFromSection(invItem.section));
  const invVal = invValue(invItem);
  // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
  if (bomVal == null || invVal == null) return false;
  return valuesEqual(bomVal, invVal);
}

/**
 * Gate for MPN prefix (step 3) and fuzzy (step 4) matches. A row is passive
 * when EITHER side says so — the BOM's designator/spec or the inventory part's
 * section — because either one alone is enough to know that MPN similarity
 * proves nothing about the value. Non-passives keep MPN similarity as their
 * evidence (DRV8301DCAR vs DRV8301DCAX).
 * @param {object} bom
 * @param {import('./types.js').InventoryItem} invItem
 * @returns {boolean}
 */
export function isFuzzyMatchValid(bom, invItem) {
  const bomType = bomComponentType(bom);
  const invType = componentTypeFromSection(invItem.section);
  if (!bomType && !invType) return true;
  if (bomType && invType && bomType !== invType) return false;
  return valuesCompatible(bom, invItem, bomType || invType)
    && footprintsCompatible(bom, invItem)
    && voltageAdequate(bom, invItem);
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
    const val = invValue(item);
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

  const bomType = bomComponentType(bom);

  // O(1) lookup when component type is known
  if (bomType) {
    const key = valueKey(bomType, bomVal);
    const candidates = invByValue[key] || [];
    let best = null, bestQty = -1;
    for (let i = 0; i < candidates.length; i++) {
      if (!footprintsCompatible(bom, candidates[i])) continue;
      if (!voltageAdequate(bom, candidates[i])) continue;
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
      const invVal = invValue(item);
      // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
      if (invVal == null || !valuesEqual(bomVal, invVal)) continue;
      if (!footprintsCompatible(bom, item)) continue;
      if (!voltageAdequate(bom, item)) continue;
      if (item.qty > bestQty) { best = item; bestQty = item.qty; }
    }
  }
  return best;
}

// ── Find alternatives (same type + value, different part) ──

// Alternates must physically fit the BOM footprint: filter with
// footprintsCompatible, same as findValueMatch. When either side's package code
// is unknown the check is permissive, so callers that need to tell verified
// alternates from unverified ones use altFootprintUnverified. A capacitor
// alternate rated below the BOM line's known voltage is dropped, same as in
// findValueMatch — it would "cover" a shortfall with a part that cannot go on
// the board.
export function findAlternatives(bom, primaryInv, invByValue) {
  if (!primaryInv) return [];
  let bomType = bomComponentType(bom);
  if (!bomType) bomType = componentTypeFromSection(primaryInv.section);
  if (!bomType) return [];
  const val = invValue(primaryInv);
  // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
  if (val == null) return [];
  const key = valueKey(bomType, val);
  const candidates = invByValue[key] || [];
  return candidates.filter(function(c) {
    return c !== primaryInv && footprintsCompatible(bom, c) && voltageAdequate(bom, c);
  });
}

/**
 * True when the BOM line has a known footprint code but the alternate's package
 * could not be determined, so it passed findAlternatives without its fit being
 * checked.
 * @param {object} bom
 * @param {import('./types.js').InventoryItem} alt
 * @returns {boolean}
 */
export function altFootprintUnverified(bom, alt) {
  return !!bomFootprintCode(bom) && !invFootprintCode(alt);
}

// ── 5-step BOM matching ──

export function matchBOM(aggregated, inventory, manualLinks, confirmedMatches, genericParts) {
  const maps = buildLookupMaps(inventory);
  const { invByLCSC, invByMPN, invByValue } = maps;
  const results = [];
  const footprintNearMisses = [];

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
      const bomType = bomComponentType(bom);
      const bomCode = bomFootprintCode(bom);
      const bomPkg = (bom.footprint || "").toUpperCase();
      const bomVolts = extractBomVoltage(bom);

      for (const gp of genericParts) {
        // Check type compatibility
        const gpType = gp.part_type === "capacitor" ? "C"
                     : gp.part_type === "resistor" ? "R"
                     : gp.part_type === "inductor" ? "L" : null;
        if (bomType && gpType && bomType !== gpType) continue;

        // Check value from spec — both known and equal, as everywhere else
        const bomVal = extractBomValue(bom, gpType);
        const specVal = parseEEValue(gp.spec.value) ?? extractValueFromDesc(gp.spec.value);
        // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
        if (specVal == null || bomVal == null) continue;
        if (!valuesEqual(specVal, bomVal)) continue;

        // Check package from spec: canonical codes when both sides have one
        // (so a spec-supplied package counts), else the legacy substring test.
        const gpPkg = (gp.spec.package || "").toUpperCase();
        const gpCode = extractFootprintCode(gp.spec.package);
        if (bomCode && gpCode) {
          if (bomCode !== gpCode) continue;
        } else if (bomPkg && gpPkg && !bomPkg.includes(gpPkg) && !gpPkg.includes(bomPkg)) continue;

        // A group that is itself defined at a lower voltage cannot serve this line.
        const gpVolts = typeof gp.spec.voltage === "number" ? gp.spec.voltage : null;
        // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
        if ((bomType || gpType) === "C" && bomVolts != null && gpVolts != null && gpVolts < bomVolts * (1 - VALUE_TOLERANCE)) continue;

        // Match found — resolve to the best member that is in inventory and
        // not rated below the line (a group can mix voltages when its own
        // spec does not pin one).
        if (gp.members && gp.members.length > 0) {
          // Sort: preferred first, then by quantity descending
          const sorted = [...gp.members].sort((a, b) => {
            if (a.preferred !== b.preferred) return b.preferred - a.preferred;
            return b.quantity - a.quantity;
          });
          let found = null;
          for (const m of sorted) {
            const item = invByLCSC[m.part_id.toUpperCase()] || invByMPN[m.part_id.toUpperCase()];
            if (item && voltageAdequate(bom, item)) { found = item; break; }
          }
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

    // 5a. Near-miss harvest: if nothing matched but there is a same-type, same-value
    // inventory part whose footprint differs, record it for the inventory panel to surface.
    if (!inv) {
      const bomVal = extractBomValue(bom);
      const bomType = bomComponentType(bom);
      const bomCode = bomFootprintCode(bom);
      // eslint-disable-next-line eqeqeq -- intentional: catches both null and undefined
      if (bomVal != null && bomType && bomCode) {
        const k = valueKey(bomType, bomVal);
        const candidates = invByValue[k] || [];
        for (let i = 0; i < candidates.length; i++) {
          const cand = candidates[i];
          const candCode = invFootprintCode(cand);
          if (candCode && candCode !== bomCode) {
            footprintNearMisses.push({
              bomKey: bk,
              bomRefs: bom.refs || "",
              bomValue: bom.value || "",
              bomFootprintCode: bomCode,
              inv: cand,
              invPartKey: cand.lcsc || cand.mpn || "",
              invPackage: cand.package || candCode,
            });
          }
        }
      }
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

    const matchSignals = {
      value: false,
      footprint: false,
      mpn: false,
    };
    if (inv) {
      if (matchType === 'lcsc' || matchType === 'mpn' || matchType === 'manual' || matchType === 'confirmed') {
        matchSignals.mpn = true;
      } else if (matchType === 'fuzzy') {
        matchSignals.mpn = true;
        matchSignals.value = valuesCompatible(bom, inv);
        matchSignals.footprint = footprintsCompatible(bom, inv);
      } else if (matchType === 'value' || matchType === 'generic') {
        matchSignals.value = true;
        matchSignals.footprint = footprintsCompatible(bom, inv);
      }
    }

    const alts = findAlternatives(bom, inv, invByValue);
    const altsUnverified = alts.filter(a => altFootprintUnverified(bom, a));

    results.push({
      bom, inv, status, matchType, alts, altsUnverified,
      matchSignals,
      genericPartId: genericPartId || null,
      genericPartName: genericPartName || null,
      genericMembers: genericMembers || null,
    });
  });

  return { results, footprintNearMisses };
}
