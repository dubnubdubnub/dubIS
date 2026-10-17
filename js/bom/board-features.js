// @ts-check
/* bom/board-features.js — Is this BOM line a board feature rather than a part?

   KiCad (and most EDA tools) put every symbol with a footprint on the BOM,
   including ones that are pure copper: fiducials, mounting holes, bare test
   pads, Tag-Connect programming footprints. With no MPN column the BOM parser
   falls back to Value as the part number (detectBOMColumns in csv-parser.js),
   so "Fiducial" became a part — status `missing`, counted in UTBP and Missing,
   offered a Link button, and sent to the cart as a raw entry. These rows get
   the `board-only` status instead (js/bom-status.js): visible, greyed, and out
   of every count and action.

   The rule is deliberately two-sided, because the same footprint name can be
   on a real part:
     1. A line carrying a supplier part number is a real part, whatever its
        footprint says — a Keystone test loop with an LCSC code, a press-fit
        standoff placed on a MountingHole footprint, a programming header with
        an MPN. Only a line with NO part number of its own is a candidate.
     2. A candidate is a board feature when its footprint (library or name)
        names one — or, when the BOM has no footprint, its value does.
   Separately, the design's own "Exclude from BOM" flag always means
   board-only: the author already said there is nothing to buy. */

import { extractPartIds, isExcludedFromBom } from '../csv-parser.js';

// Footprint patterns, tested against the whole "Library:Name" string.
// Ordered; the first hit names the reason.
const FOOTPRINT_FEATURES = [
  { re: /fiducial/i, reason: "Fiducial — board feature, nothing to place" },
  { re: /mounting[_ -]?hole/i, reason: "Mounting hole — board feature, nothing to place" },
  // Bare copper only. KiCad's TestPoint library also carries real parts —
  // TestPoint_Keystone_* (a test loop you buy), TestPoint_Loop_* (a wire you
  // solder in), TestPoint_Bridge_* — none of which match these three shapes.
  {
    re: /(^|[:_])TestPoint_(Pad|Plated_Hole|THTPad)(_|$)/i,
    reason: "Bare test pad — board feature, nothing to place",
  },
  // "Tag-Connect" or a TC2030/TC2050/TC2070 not glued to a longer part
  // number (LTC2050 is a real op-amp).
  {
    re: /tag[-_ ]?connect|(^|[^a-z0-9])TC20[357]0(?![0-9])/i,
    reason: "Tag-Connect footprint — programming pads, nothing to place",
  },
];

// Value patterns, used only when the line has no footprint at all. Anchored,
// because a value is free text: "LTC2050" is an op-amp, not a TC2050.
const VALUE_FEATURES = [
  { re: /^fiducial(\b|_)/i, reason: FOOTPRINT_FEATURES[0].reason },
  { re: /^mounting[_ -]?hole(\b|_)/i, reason: FOOTPRINT_FEATURES[1].reason },
  { re: /^test[_ -]?point(\b|_|$)/i, reason: FOOTPRINT_FEATURES[2].reason },
  { re: /^(tag[-_ ]?connect|TC20[357]0)(?![0-9a-z])/i, reason: FOOTPRINT_FEATURES[3].reason },
];

export const EXCLUDED_FROM_BOM_REASON = "Excluded from BOM in the design — nothing to buy or place";

/**
 * True when the line names a part number of its own: an LCSC code, or an MPN
 * that is not merely the Value copied in by the parser's Value-as-MPN fallback.
 * @param {{ lcsc?: string, mpn?: string, value?: string }} bom
 * @returns {boolean}
 */
export function hasPartNumber(bom) {
  if ((bom.lcsc || "").trim()) return true;
  const mpn = (bom.mpn || "").trim().toUpperCase();
  const value = (bom.value || "").trim().toUpperCase();
  return !!mpn && mpn !== value;
}

/**
 * Why this BOM line is a board feature, or null when it is a real part.
 * The returned string is user-facing: it is the row's label and tooltip.
 * @param {{ lcsc?: string, mpn?: string, value?: string, footprint?: string, excluded?: boolean } | null | undefined} bom
 * @returns {string|null}
 */
export function boardFeatureReason(bom) {
  if (!bom) return null;
  if (!hasPartNumber(bom)) {
    const footprint = (bom.footprint || "").trim();
    const table = footprint ? FOOTPRINT_FEATURES : VALUE_FEATURES;
    const subject = footprint || (bom.value || "").trim();
    if (subject) {
      for (const f of table) {
        if (f.re.test(subject)) return f.reason;
      }
    }
  }
  if (bom.excluded) return EXCLUDED_FROM_BOM_REASON;
  return null;
}

/**
 * boardFeatureReason for a raw staging-table row, read through the detected
 * columns — the same fields aggregateBomRows puts on the aggregated line, so
 * the staging row and the inventory view agree on what is a board feature.
 * @param {string[]} row
 * @param {{ lcsc: number, lcscCols?: number[], mpn: number, value: number, footprint: number, exclude?: number }} cols
 * @returns {string|null}
 */
export function rawRowBoardReason(row, cols) {
  const { lcsc, mpn } = extractPartIds(row, cols);
  const cell = (/** @type {number|undefined} */ ci) =>
    (ci !== undefined && ci !== -1) ? (row[ci] || "").trim() : "";
  const exclude = cols.exclude ?? -1;
  return boardFeatureReason({
    lcsc, mpn,
    value: cell(cols.value),
    footprint: cell(cols.footprint),
    excluded: exclude !== -1 && isExcludedFromBom(row[exclude]),
  });
}
