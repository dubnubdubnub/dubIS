// @ts-check
/* bom-row-data.js — Pure display-data computation for BOM table rows.
   No DOM dependencies. Depends on: bomKey, STATUS_ICONS,
   STATUS_ROW_CLASS (from part-keys.js). */

import { bomKey, STATUS_ICONS, STATUS_ROW_CLASS } from './part-keys.js';

export function bomRowDisplayData(r, query, activeFilter, expandedAlts, linkingState, expandedMembers) {
  var st = r.effectiveStatus;

  // ── Filter by status ──
  if (activeFilter !== "all" && st !== activeFilter) {
    var matchesFilter =
      (activeFilter === "manual" && st === "manual-short") ||
      (activeFilter === "confirmed" && st === "confirmed-short") ||
      (activeFilter === "generic" && st === "generic-short") ||
      (activeFilter === "short" && (st === "manual-short" || st === "confirmed-short" || st === "generic-short"));
    if (!matchesFilter) return null;
  }

  // ── Filter by search query ──
  if (query) {
    var text = [
      r.bom.lcsc, r.bom.mpn, r.bom.value, r.bom.refs, r.bom.desc,
      r.inv ? r.inv.lcsc : "", r.inv ? r.inv.mpn : "", r.inv ? r.inv.description : "",
    ].join(" ").toLowerCase();
    if (!text.includes(query)) return null;
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
  var invDesc = hasInv
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
  var qtyClass = st === "dnp" ? "qty-dnp"
    : st === "manual" ? "qty-manual"
    : st === "manual-short" ? "qty-manual-short"
    : st === "confirmed" ? "qty-confirmed"
    : st === "confirmed-short" ? "qty-confirmed-short"
    : st === "generic" ? "qty-generic"
    : st === "generic-short" ? "qty-generic-short"
    : st === "ok" ? "qty-ok"
    : st === "short" ? (r.coveredByAlts ? "qty-ok" : "qty-short")
    : st === "possible" ? "qty-possible"
    : "qty-miss";

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
  var showGroupFlyout = !!(r.genericPartId) || ((st === "missing" || st === "possible") && !hasInv && !!(r.bom.value || r.bom.footprint));

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
  };
}
