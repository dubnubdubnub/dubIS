// @ts-check
function clean(d) {
  const s = (d === null || d === undefined ? '' : String(d)).trim();
  return (s.toLowerCase() === 'nan' || s.toLowerCase() === 'none') ? '' : s;
}

/**
 * Pick the best fetched description from distributor rows.
 * Preference: pinned row → cheapest row → first row with any description.
 * @param {Array<{description?:string}>} rows
 * @param {number} pinnedIndex
 * @param {number} cheapestIndex
 * @returns {string}
 */
export function pickBestDescription(rows, pinnedIndex, cheapestIndex) {
  if (!rows || !rows.length) return '';
  for (const i of [pinnedIndex, cheapestIndex]) {
    if (i >= 0 && i < rows.length) {
      const d = clean(rows[i].description);
      if (d) return d;
    }
  }
  for (const r of rows) {
    const d = clean(r.description);
    if (d) return d;
  }
  return '';
}
