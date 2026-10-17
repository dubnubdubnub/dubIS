// @ts-check
/* bom-row-data.js — Pure display-data computation for BOM table rows.
   No DOM dependencies. Depends on: bomKey, STATUS_ICONS,
   STATUS_ROW_CLASS (from part-keys.js), the status registry (bom-status.js). */

import { bomKey, invPartKey, STATUS_ICONS, STATUS_ROW_CLASS } from './part-keys.js';
import { statusSpec } from './bom-status.js';
import { extractFootprintCode, invFootprintCode } from './matching.js';
import { matchesCategoryName } from './category-match.js';

/**
 * @param {Object} r - a BOM comparison row ({ bom, inv, effectiveStatus, … })
 * @param {string} query - lowercase search term
 * @param {string} activeFilter
 * @param {Set<string>} expandedAlts
 * @param {Object} linkingState
 * @param {Set<string>} expandedMembers
 * @param {{categoryHit?: boolean}} [options]
 * @returns {Object|null} the row's display data, or null to hide the row
 */
export function bomRowDisplayData(r, query, activeFilter, expandedAlts, linkingState, expandedMembers, options) {
  var st = r.effectiveStatus;
  var spec = statusSpec(st);

  // ── Filter by status ──
  // A filter button is a countStatuses bucket, so a row shows under every
  // bucket its status counts toward (manual-short under Manual AND Short).
  if (activeFilter !== "all" && st !== activeFilter) {
    if (!(spec && spec.buckets.includes(activeFilter))) return null;
  }

  // ── Filter by search query ──
  if (query) {
    // DECISION for the category toggle in BOM mode, stated here because it is
    // not the same claim as the inventory grid's and cannot be.
    //
    // The grid's rule is "keep the whole section bucket", which needs a bucket.
    // A BOM table has none — its rows are BOM lines, bucketed by nothing, and
    // the BOM side of a row (`r.bom`) carries no `section` at all: a section is
    // a property of a part we stock, not of a line on somebody's bill of
    // materials. So "show the whole category" is not expressible here, and
    // inventing a bucket would mean showing BOM lines that match nothing, which
    // is the opposite of what a BOM search is for.
    //
    // What IS expressible, and is what this does: a BOM line whose MATCHED
    // inventory part belongs to the named category stays visible. That is the
    // same question — "is this row in the category I just typed?" — answered
    // with the only section a BOM row has, and it reads as "show me my BOM lines
    // that are diodes". A line matched to nothing has no section and so is
    // filtered by text exactly as before; the toggle never ADDS an unmatched
    // line to the table.
    var categoryHit = !!(options && options.categoryHit
      && r.inv && matchesCategoryName(query, r.inv.section || ""));
    var text = [
      r.bom.lcsc, r.bom.mpn, r.bom.value, r.bom.refs, r.bom.desc,
      r.inv ? r.inv.lcsc : "", r.inv ? r.inv.mpn : "", r.inv ? r.inv.description : "",
    ].join(" ").toLowerCase();
    if (!categoryHit && !text.includes(query)) return null;
  }

  var partKey = bomKey(r.bom);
  var hasInv = !!r.inv;

  // ── Row class and icon ──
  var coveredShort = st === "short" && r.coveredByAlts;
  var rowClass = coveredShort ? "row-yellow-covered" : (STATUS_ROW_CLASS[st] || "row-red");
  var icon = coveredShort ? "~+" : (STATUS_ICONS[st] || "\u2014");

  // ── Display values ──
  var dispLcsc = (hasInv ? r.inv.lcsc : "") || r.bom.lcsc || "";
  var dispDigikey = (hasInv ? r.inv.digikey : "") || "";
  var dispPololu = (hasInv ? r.inv.pololu : "") || "";
  var dispMouser = (hasInv ? r.inv.mouser : "") || "";
  var dispMpn = (hasInv ? r.inv.mpn : "") || r.bom.mpn || "";
  var invQty = hasInv ? r.inv.qty : "\u2014";
  // A board-only row says WHY it is out of the counts, in the column a
  // missing row would say "not in inventory".
  var boardReason = st === "board-only" ? (r.boardReason || "board feature") : "";
  var invDesc = boardReason
    ? boardReason
    : hasInv
    ? (r.inv.description || r.inv.mpn)
    : (r.bom.desc || r.bom.value || "not in inventory");

  // ── Match label ──
  var matchLabel = r.matchType === "lcsc" ? "LCSC"
    : r.matchType === "mpn" ? "MPN"
    : r.matchType === "fuzzy" ? "Fuzzy"
    : r.matchType === "value" ? "Value"
    : r.matchType === "manual" ? "Manual"
    : r.matchType === "confirmed" ? "Confirmed"
    : r.matchType === "generic" ? "Generic"
    : "\u2014";

  // ── Quantity CSS class ──
  // A plain short row whose alternates cover it reads as ok; anything the
  // registry does not declare reads as missing.
  var qtyClass = coveredShort ? "qty-ok" : (spec ? spec.qtyClass : "qty-miss");

  // ── Alt badge data ──
  var altBadge = null;
  if (r.alts && r.alts.length > 0) {
    var altS = r.alts.length === 1 ? "alt" : "alts";
    var badgeText, covered;
    if (st === "short" || st === "manual-short" || st === "confirmed-short") {
      badgeText = r.coveredByAlts ? "\u2714 covers" : "still short";
      covered = !!r.coveredByAlts;
    } else {
      badgeText = r.alts.length + " " + altS;
      covered = true;
    }
    altBadge = {
      altQty: r.altQty,
      badgeText: badgeText,
      covered: covered,
      expanded: expandedAlts.has(partKey),
      // Alts whose package is unknown: counted in altQty, but their fit to the
      // BOM footprint was never checked.
      unverifiedCount: (r.altsUnverified || []).length,
    };
  }

  // ── Generic member badge ──
  var memberBadge = null;
  var genericPartName = r.genericPartName || null;
  if (r.matchType === "generic" && r.genericMembers && r.genericMembers.length > 1) {
    memberBadge = {
      groupName: r.genericPartName,
      memberCount: r.genericMembers.length,
      expanded: expandedMembers ? expandedMembers.has(partKey) : false,
    };
  }

  // ── Group flyout eligibility ──
  // Show for: rows already in a group, OR missing/possible rows with value/footprint info.
  // Note: "possible" status implies hasInv (value match), but we still show the flyout
  // so users can manage groups for value-only BOM rows that fuzzy-matched inventory.
  var showGroupFlyout = !!(r.genericPartId) || ((st === "missing" || st === "possible") && !!(r.bom.value || r.bom.footprint));

  // ── Footprint confirmation hint (only meaningful for value/fuzzy matches) ──
  // Show the "+CODE" badge only when we can actually verify the match: both BOM
  // and inventory must yield an extractable footprint code, and they must agree.
  // matchSignals.footprint defaults permissive when data is missing, so we re-check
  // here to avoid claiming a confirmation that was never earned.
  var footprintConfirmed = false;
  var footprintCode = null;
  if (r.matchSignals && r.matchSignals.value && r.matchSignals.footprint &&
      (r.matchType === 'value' || r.matchType === 'fuzzy')) {
    var bomCode = extractFootprintCode(r.bom.footprint);
    var invCode = invFootprintCode(r.inv);
    if (bomCode && invCode && bomCode === invCode) {
      footprintConfirmed = true;
      footprintCode = bomCode;
    }
  }

  // ── Button visibility ──
  var showConfirm = st === "possible" && hasInv;
  var showUnconfirm = (st === "confirmed" || st === "confirmed-short") && hasInv;
  var showAdjust = hasInv;
  var showLink = hasInv || st === "missing";

  // ── Link button active state ──
  var linkActive = false;
  if (hasInv) {
    linkActive = linkingState.linkingMode && linkingState.linkingInvItem === r.inv;
  } else if (st === "missing") {
    linkActive = linkingState.linkingMode && linkingState.linkingBomRow &&
      bomKey(linkingState.linkingBomRow.bom) === bomKey(r.bom);
  }

  // ── Linking highlights ──
  var isLinkingSource = linkingState.linkingMode && linkingState.linkingInvItem === r.inv;
  var isReverseLinkingSource = linkingState.linkingMode && linkingState.linkingBomRow &&
    bomKey(linkingState.linkingBomRow.bom) === partKey;
  var isReverseTarget = linkingState.linkingMode && linkingState.linkingBomRow &&
    hasInv && !isReverseLinkingSource;

  return {
    partKey: partKey,
    // Inventory part key (matches invPartKey on the inventory side) — used to
    // key label-export selection checkboxes so a part selected on either side
    // shares one key. Empty for missing rows (no inventory item).
    invKey: hasInv ? invPartKey(r.inv) : "",
    status: st,
    rowClass: rowClass,
    icon: icon,
    dispLcsc: dispLcsc,
    dispDigikey: dispDigikey,
    dispPololu: dispPololu,
    dispMouser: dispMouser,
    dispMpn: dispMpn,
    effectiveQty: r.effectiveQty,
    invQty: invQty,
    invDesc: invDesc,
    matchLabel: matchLabel,
    qtyClass: qtyClass,
    refs: r.bom.refs || "",
    isMissing: st === "missing",
    boardReason: boardReason,
    altBadge: altBadge,
    showConfirm: showConfirm,
    showUnconfirm: showUnconfirm,
    showAdjust: showAdjust,
    showLink: showLink,
    linkActive: linkActive,
    isLinkingSource: isLinkingSource,
    isReverseLinkingSource: isReverseLinkingSource,
    isReverseTarget: isReverseTarget,
    showAlts: !!(r.alts && r.alts.length > 0 && expandedAlts.has(partKey)),
    showMembers: !!(memberBadge && memberBadge.expanded),
    memberBadge: memberBadge,
    genericPartName: genericPartName,
    genericMembers: r.genericMembers || null,
    showGroupFlyout: showGroupFlyout,
    genericPartId: r.genericPartId || null,
    bomValue: r.bom.value || "",
    bomFootprint: r.bom.footprint || "",
    bomRefs: r.bom.refs || "",
    hasInv: hasInv,
    footprintConfirmed: footprintConfirmed,
    footprintCode: footprintCode,
  };
}
