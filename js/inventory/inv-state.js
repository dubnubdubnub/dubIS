/* inventory/inv-state.js — Centralized mutable state for the inventory panel. */

var state = {
  body: null,              // set in init()
  searchInput: null,       // set in init()
  clearFilterBtn: null,    // set in init()
  distFilterBar: null,     // set in init()

  collapsedSections: new Set(),
  bomData: null,           // { rows, fileName, multiplier }
  activeFilter: "all",
  activeDistributors: new Set(), // empty = show all, or set of "lcsc"|"digikey"|"mouser"|"pololu"|"other"
  expandedAlts: new Set(),
  expandedMembers: new Set(),
  rowMap: new Map(),       // partKey -> r, rebuilt each render

  // Groups mode state
  groupsSections: new Set(),   // sections with groups mode active
  expandedGroups: new Set(),   // expanded generic group IDs
  groupFilters: {},            // { genericPartId: { dielectric: "C0G", tolerance: "10%" } }

  // Flyout state
  activeFlyoutId: null,       // generic_part_id of the active flyout
  linkedSearchText: "",       // synced search text between active flyout and main inventory
  flyoutDragActive: false,    // true when any flyout is open (shows drag handles on inventory rows)

  // Hide descriptions when panel is too narrow for readable text
  DESC_HIDE_WIDTH: 680,
  hideDescs: true,

  // Near-miss map: invPartKey.toUpperCase() → near-miss object (populated on BOM match)
  nearMissMap: null,

  // ── Column-header controls (sort + group) ──
  groupLevel: 0,           // 0 = full hierarchy (default), 1 = sections only, 2 = flat
  sortColumn: null,        // null | "mpn" | "description" | "qty" | "unit_price" | "value"
  sortScope: null,         // null | "subsection" | "section" | "global"
  vendorGroupScope: null,  // null | "subsection" | "section" | "global"
};

export default state;

export function hydrateFromPreferences(view) {
  if (!view || typeof view !== "object") return;
  if (Number.isInteger(view.group_level) && view.group_level >= 0 && view.group_level <= 2) {
    state.groupLevel = view.group_level;
  }
  state.sortColumn       = view.sort_column || null;
  state.sortScope        = view.sort_scope  || null;
  state.vendorGroupScope = view.vendor_group_scope || null;
}
