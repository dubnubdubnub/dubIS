// @ts-check
/* inv-sort-group.js — Pure helpers for the inventory column-header
   scope-cycling sort and vendor-grouping. No DOM, no store, no events. */

/**
 * Advance the scope cycle for a column based on the current Group level.
 * Returns the next scope string, or null to mean "off / default".
 *
 * groupLevel=0 (full hierarchy): subsection → section → global → null
 * groupLevel=1 (sections only):           section → global → null
 * groupLevel=2 (flat):                              global → null
 *
 * If the current scope is not reachable at the given level (e.g. user
 * lowered the Group level while a finer-grained scope was active),
 * coerces forward to the first reachable scope.
 *
 * @param {number} groupLevel  0 | 1 | 2
 * @param {string|null} current  null | "subsection" | "section" | "global"
 * @returns {string|null}
 */
export function nextScope(groupLevel, current) {
  var cycle;
  if (groupLevel === 0) cycle = ['subsection', 'section', 'global'];
  else if (groupLevel === 1) cycle = ['section', 'global'];
  else cycle = ['global'];

  if (current === null) return cycle[0];

  var idx = cycle.indexOf(current);
  if (idx === -1) return cycle[0];
  if (idx === cycle.length - 1) return null;
  return cycle[idx + 1];
}
