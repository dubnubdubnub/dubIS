/* inventory/inv-state.js — Centralized mutable state for the inventory panel. */

var state = {
  body: null,              // set in init()
  searchInput: null,       // set in init()
  clearFilterBtn: null,    // set in init()
  distFilterBar: null,     // set in init()

  collapsedSections: new Set(),
  bomData: null,           // { rows, fileName, multiplier }
  activeFilter: "all",
  activeDistributor: null, // null = show all, or "lcsc"|"digikey"|"mouser"|"pololu"|"other"
  expandedAlts: new Set(),
  expandedMembers: new Set(),
  rowMap: new Map(),       // partKey -> r, rebuilt each render

  // Groups mode state
  groupsSections: new Set(),   // sections with groups mode active
  expandedGroups: new Set(),   // expanded generic group IDs
  groupFilters: {},            // { genericPartId: { dielectric: "C0G", tolerance: "10%" } }

  // Hide descriptions when panel is too narrow for readable text
  DESC_HIDE_WIDTH: 680,
  hideDescs: true,
};

export default state;
