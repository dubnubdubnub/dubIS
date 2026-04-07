/* inventory/inv-state.js — Centralized mutable state for the inventory panel. */

var state = {
  body: null,              // set in init()
  searchInput: null,       // set in init()

  collapsedSections: new Set(),
  bomData: null,           // { rows, fileName, multiplier }
  activeFilter: "all",
  expandedAlts: new Set(),
  expandedMembers: new Set(),
  rowMap: new Map(),       // partKey -> r, rebuilt each render

  // Hide descriptions when panel is too narrow for readable text
  DESC_HIDE_WIDTH: 680,
  hideDescs: true,
};

export default state;
