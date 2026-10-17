// @ts-check
/* bom-status.js — The one declaration of every effective BOM status.

   A BOM row's `effectiveStatus` (computed by computeRows in
   js/bom/bom-logic.js) used to be re-interpreted in a dozen places, each with
   its own hand-written OR-chain: which statuses count as "short", which ones
   UTBP skips, which are linkable, which go to the cart, which get consumed,
   their icon, row colour, qty colour, sort weight and filter button. Adding a
   status meant a shotgun edit where forgetting one place was silent — a row
   that is not in the cart's chain simply never gets bought, and nothing says so.

   Every one of those questions is now a property here, and every consumer
   reads the property instead of naming statuses. A new status is one entry in
   BOM_STATUSES; tests/js/property/bom-status.property.test.mjs fails if
   computeRows can emit a status this table does not declare, or if an entry
   is missing a property.

   Dependency-free on purpose: part-keys.js, bom/, inventory/ and the tests all
   import it, and none of them may drag the store (or js/constants.js's
   top-level fetch) into a pure test. */

/**
 * @typedef {Object} BomStatusSpec
 * @property {string[]} buckets  The countStatuses counters this status
 *   increments — which are also the inventory panel's filter buttons it shows
 *   under (a `manual-short` row is both "short" and "manual"). Every entry
 *   must name at least one bucket from BOM_BUCKETS.
 * @property {boolean} placed  Counts toward UTBP ("unique to be placed").
 * @property {boolean} linkable  A valid target for a manual link (forward
 *   linking from an inventory part to the BOM row).
 * @property {boolean} needsPurchase  Sent to the cart by "Add missing to cart".
 * @property {boolean} consumable  Deducted by "Consume from inventory" (the row
 *   also needs a matched inventory part with a part key).
 * @property {string} icon  Status-cell glyph.
 * @property {string} rowClass  <tr> class (css/tables.css).
 * @property {string} qtyClass  Need/Have cell class (css/tables.css).
 * @property {number} sortWeight  Inventory BOM-view sort: low sorts first.
 * @property {string} label  Human name, used in tooltips and docs.
 */

/** @type {Readonly<Record<string, Readonly<BomStatusSpec>>>} */
export const BOM_STATUSES = Object.freeze({
  ok: Object.freeze({
    buckets: ["ok"], placed: true, linkable: false, needsPurchase: false, consumable: true,
    icon: "+", rowClass: "row-green", qtyClass: "qty-ok", sortWeight: 3, label: "in stock",
  }),
  short: Object.freeze({
    buckets: ["short"], placed: true, linkable: true, needsPurchase: true, consumable: true,
    icon: "~", rowClass: "row-yellow", qtyClass: "qty-short", sortWeight: 2, label: "short",
  }),
  possible: Object.freeze({
    // A value/fuzzy guess: never consumed until the user confirms it.
    buckets: ["possible"], placed: true, linkable: true, needsPurchase: true, consumable: false,
    icon: "?", rowClass: "row-orange", qtyClass: "qty-possible", sortWeight: 1, label: "possible match",
  }),
  missing: Object.freeze({
    buckets: ["missing"], placed: true, linkable: true, needsPurchase: true, consumable: false,
    icon: "—", rowClass: "row-red", qtyClass: "qty-miss", sortWeight: 0, label: "missing",
  }),
  manual: Object.freeze({
    buckets: ["manual"], placed: true, linkable: false, needsPurchase: false, consumable: true,
    icon: "✦", rowClass: "row-pink", qtyClass: "qty-manual", sortWeight: 0.5, label: "manual link",
  }),
  "manual-short": Object.freeze({
    buckets: ["short", "manual"], placed: true, linkable: true, needsPurchase: true, consumable: true,
    icon: "✦~", rowClass: "row-pink-short", qtyClass: "qty-manual-short", sortWeight: 0.4, label: "manual link, short",
  }),
  confirmed: Object.freeze({
    buckets: ["confirmed"], placed: true, linkable: false, needsPurchase: false, consumable: true,
    icon: "✔", rowClass: "row-teal", qtyClass: "qty-confirmed", sortWeight: 0.75, label: "confirmed match",
  }),
  "confirmed-short": Object.freeze({
    buckets: ["short", "confirmed"], placed: true, linkable: true, needsPurchase: true, consumable: true,
    icon: "✔~", rowClass: "row-teal-short", qtyClass: "qty-confirmed-short", sortWeight: 0.7, label: "confirmed match, short",
  }),
  generic: Object.freeze({
    // sortWeight 0 is inherited, not chosen: generic was never in the old
    // BOM_STATUS_SORT_ORDER table, so it fell through `|| 0` and sorted with
    // the missing rows. Kept identical by this refactor; a deliberate weight
    // (beside `confirmed`, say) is a one-number change here.
    buckets: ["generic"], placed: true, linkable: false, needsPurchase: false, consumable: true,
    icon: "◆", rowClass: "row-purple", qtyClass: "qty-generic", sortWeight: 0, label: "generic group",
  }),
  "generic-short": Object.freeze({
    buckets: ["short", "generic"], placed: true, linkable: true, needsPurchase: true, consumable: true,
    icon: "◆~", rowClass: "row-purple-short", qtyClass: "qty-generic-short", sortWeight: 0, label: "generic group, short",
  }),
  dnp: Object.freeze({
    // A real part deliberately not placed. Not consumed: the cart never buys
    // a DNP part, so deducting its stock on every build would walk inventory
    // down for parts that never left the reel. (Before this registry a DNP row
    // WAS consumed whenever it matched by LCSC/MPN — prepareConsumption looked
    // at matchType only.)
    buckets: ["dnp"], placed: false, linkable: false, needsPurchase: false, consumable: false,
    icon: "✖", rowClass: "row-dnp", qtyClass: "qty-dnp", sortWeight: 4, label: "do not place",
  }),
  "board-only": Object.freeze({
    // A board feature — fiducial, mounting hole, bare test pad, Tag-Connect
    // footprint, or anything the design marks "Exclude from BOM". Copper, not
    // a part: nothing to buy, place, link or consume. Still listed (greyed,
    // with its reason) so nothing in the user's BOM silently disappears.
    // See js/bom/board-features.js for what qualifies.
    buckets: ["boardOnly"], placed: false, linkable: false, needsPurchase: false, consumable: false,
    icon: "◌", rowClass: "row-board-only", qtyClass: "qty-board-only", sortWeight: 5, label: "board feature",
  }),
});

/** Every declared status id, in declaration order. */
export const BOM_STATUS_IDS = Object.freeze(Object.keys(BOM_STATUSES));

/**
 * The counters countStatuses keeps, which are also the inventory panel's BOM
 * filter buttons and the BOM panel's summary chips, in display order.
 *
 * `hideWhenZero` drops both the filter button and the chip when nothing is in
 * the bucket; `chip` is null for a bucket the summary has never shown (generic).
 * @typedef {Object} BomBucketSpec
 * @property {string} id  countStatuses key and the filter button's data-filter.
 * @property {string} filterLabel
 * @property {{ cls: string, label: string, title?: string } | null} chip
 * @property {boolean} hideWhenZero
 */

/** @type {ReadonlyArray<Readonly<BomBucketSpec>>} */
export const BOM_BUCKETS = Object.freeze([
  { id: "manual", filterLabel: "Manual", chip: { cls: "pink", label: "manual" }, hideWhenZero: true },
  { id: "confirmed", filterLabel: "Confirmed", chip: { cls: "teal", label: "confirmed" }, hideWhenZero: true },
  { id: "generic", filterLabel: "Generic", chip: null, hideWhenZero: true },
  { id: "ok", filterLabel: "In Stock", chip: { cls: "green", label: "ok" }, hideWhenZero: false },
  { id: "short", filterLabel: "Short", chip: { cls: "yellow", label: "short" }, hideWhenZero: false },
  { id: "possible", filterLabel: "Possible", chip: { cls: "orange", label: "possible" }, hideWhenZero: false },
  { id: "missing", filterLabel: "Missing", chip: { cls: "red", label: "missing" }, hideWhenZero: false },
  { id: "dnp", filterLabel: "DNP", chip: { cls: "grey", label: "DNP" }, hideWhenZero: true },
  {
    id: "boardOnly", filterLabel: "Board-only", hideWhenZero: true,
    chip: {
      cls: "grey chip-board-only", label: "board-only",
      title: "fiducials, mounting holes, test pads and other board features — nothing to buy or place",
    },
  },
].map((b) => Object.freeze(b)));

/** The property names every BOM_STATUSES entry must carry, with their type. */
export const REQUIRED_STATUS_PROPS = Object.freeze({
  buckets: "array",
  placed: "boolean",
  linkable: "boolean",
  needsPurchase: "boolean",
  consumable: "boolean",
  icon: "string",
  rowClass: "string",
  qtyClass: "string",
  sortWeight: "number",
  label: "string",
});

/**
 * The spec for a status, or null for one this table does not declare.
 * Own-property lookup, so "constructor" or "toString" is not a status.
 * @param {string|undefined|null} status
 * @returns {Readonly<BomStatusSpec>|null}
 */
export function statusSpec(status) {
  if (typeof status !== "string") return null;
  return Object.prototype.hasOwnProperty.call(BOM_STATUSES, status) ? BOM_STATUSES[status] : null;
}

/**
 * True when `status` is declared and its boolean `flag` is set. An undeclared
 * status answers false for every flag — it is never linked, bought or consumed.
 * @param {string|undefined|null} status
 * @param {"placed"|"linkable"|"needsPurchase"|"consumable"} flag
 * @returns {boolean}
 */
export function statusHas(status, flag) {
  const spec = statusSpec(status);
  return !!(spec && spec[flag]);
}
