// @ts-check
/* cart-plan-logic.js — Pure presentation logic for the cart's purchase plan.

   The server computes the plan (domain/cart_plan.py + domain/purchase_candidates.py):
   what each line needs, every purchasable candidate, and which one the row's
   preset selects. Nothing here re-derives any of that — a second ranking
   implementation in JS would drift from the Python one the moment either
   changed, and the drift would show up as a price that disagrees with the
   order actually placed.

   What IS here: turning a plan line into the strings a row renders, and saying
   what a row is *waiting on* when it has no pick. Kept DOM-free so it can be
   tested without a browser. */

/** Presets the server understands. Order is the segmented control's order. */
export const PRESETS = ['min', 'tier_up', 'reel', 'custom'];

export const PRESET_LABELS = {
  min: 'Min',
  tier_up: 'Tier up',
  reel: 'Reel',
  custom: 'Custom',
};

export const PRESET_TITLES = {
  min: 'Cheapest total spend that covers the requirement',
  tier_up: 'Cheapest price break above the requirement',
  reel: 'Cheapest reel-carried option',
  custom: 'A quantity you type yourself',
};

/** Rejection reasons that mean "typing a different number would fix this". */
const ACTIONABLE = new Set(['below_moq', 'not_multiple', 'below_ladder']);

/**
 * Format money for a spreadsheet column: always 2dp, thousands separated.
 * @param {number|null|undefined} value
 * @returns {string}
 */
export function money(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return '$' + Number(value).toLocaleString(undefined, {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  });
}

/**
 * Unit prices run to four and five significant figures ($0.00213/ea), so the
 * 2dp used for line totals would render most of a passives BOM as "$0.00".
 * @param {number|null|undefined} value
 * @returns {string}
 */
export function unitPrice(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  const n = Number(value);
  const dp = n !== 0 && Math.abs(n) < 0.01 ? 5 : 4;
  return '$' + n.toLocaleString(undefined, {
    minimumFractionDigits: dp, maximumFractionDigits: dp,
  });
}

/**
 * @param {number|null|undefined} value
 * @returns {string}
 */
export function qty(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return Number(value).toLocaleString();
}

/**
 * How a line's requirement was arrived at, in the terms the user thinks in.
 *
 * This is the whole reason board_count is stored rather than folded into the
 * quantity: without it the row shows 5,000 and nobody can reconstruct why.
 * @param {any} line
 * @returns {string}
 */
export function derivation(line) {
  if (!line) return '';
  const parts = [];
  if (line.per_board_qty) {
    const boards = line.board_count || 1;
    const placements = line.per_board_qty;
    parts.push(`${qty(boards)} board${boards === 1 ? '' : 's'} × ${qty(placements)} placement${placements === 1 ? '' : 's'} = ${qty(line.gross_qty)}`);
  } else if (line.gross_qty) {
    parts.push(`${qty(line.gross_qty)} required`);
  }
  if (line.covered_by_stock) parts.push(`less ${qty(line.covered_by_stock)} on hand`);
  return parts.join(', ');
}

/**
 * The most actionable rejection for a row, or null.
 *
 * "Sold in multiples of 5,000" beats "no price quoted below 5,000" because
 * only one of them comes with a number the user can type. Rejections that
 * cannot be fixed by changing the quantity (out of stock, no prices at all)
 * rank last — they are still worth showing, just not as advice.
 * @param {any[]|null|undefined} rejections
 * @returns {any|null}
 */
export function bestHint(rejections) {
  if (!Array.isArray(rejections) || rejections.length === 0) return null;
  const actionable = rejections.filter(
    (r) => ACTIONABLE.has(r.reason) && Number.isFinite(r.nearest_legal),
  );
  if (actionable.length) {
    // Smallest legal quantity: the cheapest way out of the problem.
    return actionable.reduce((a, b) => (b.nearest_legal < a.nearest_legal ? b : a));
  }
  return rejections[0];
}

/**
 * One-line note for a row: why it has no pick, or what is unusual about the
 * pick it has. Empty string when the row is unremarkable — a note on every
 * row is a note nobody reads.
 * @param {any} line
 * @returns {string}
 */
export function note(line) {
  if (!line) return '';
  if (line.required_qty === 0) return line.reason || 'covered by stock on hand';
  if (!line.selected) {
    const hint = bestHint(line.rejections);
    if (hint && Number.isFinite(hint.nearest_legal)) {
      return `${hint.detail} — nearest is ${qty(hint.nearest_legal)}`;
    }
    return (hint && hint.detail) || line.reason || 'nothing purchasable';
  }
  if (line.over_ceiling) return `${line.reason} (over your reel ceiling)`;
  if (line.fell_back) return line.reason;
  if (line.selected.stock_known === false) return 'stock not observed';
  return '';
}

/**
 * What the row is buying, as a label: quantity and packaging together.
 * @param {any} candidate
 * @returns {string}
 */
export function candidateLabel(candidate) {
  if (!candidate) return '—';
  const pkg = (candidate.packaging || '').trim();
  return pkg ? `${qty(candidate.qty)} · ${pkg}` : qty(candidate.qty);
}

/**
 * How much this row's pick costs versus its runner-up. Null when there is no
 * runner-up, so the caller can omit the column rather than print "—".
 * @param {any} line
 * @returns {{delta: number, label: string}|null}
 */
export function runnerUpDelta(line) {
  if (!line || !line.selected || !line.runner_up) return null;
  const delta = Number(line.runner_up.spend) - Number(line.selected.spend);
  return {
    delta,
    label: `next best ${candidateLabel(line.runner_up)} at ${money(line.runner_up.spend)} (+${money(delta)})`,
  };
}

/**
 * Cart-level summary. `unpriced` is surfaced separately from the total because
 * a total that silently excludes rows nobody could price reads as complete
 * when it is not.
 * @param {any} plan
 * @returns {{spend: string, lines: number, covered: number, unpriced: number, caveat: string}}
 */
export function summary(plan) {
  const totals = (plan && plan.totals) || {};
  const covered = totals.covered_by_stock || 0;
  const unpriced = totals.unpriced || 0;
  const caveats = [];
  if (covered) caveats.push(`${covered} covered by stock`);
  if (unpriced) caveats.push(`${unpriced} unpriced`);
  return {
    spend: money(totals.spend || 0),
    lines: totals.lines || 0,
    covered,
    unpriced,
    caveat: caveats.join(', '),
  };
}

/**
 * Index a plan's lines by ref so a grid row can find its own plan in O(1)
 * rather than scanning for every cell of every row.
 * @param {any} plan
 * @returns {Map<string, any>}
 */
export function linesByRef(plan) {
  const map = new Map();
  for (const line of (plan && plan.lines) || []) {
    if (line && line.ref) map.set(line.ref, line);
  }
  return map;
}

/**
 * Clamp a board-count input to something the API will accept.
 *
 * Returns null for anything that is not a positive whole number, so the caller
 * can reject the keystroke rather than silently substituting a number the user
 * did not choose — a board count is a multiplier on every quantity in the
 * cart, and quietly rounding it changes what gets ordered.
 * @param {string|number} raw
 * @returns {number|null}
 */
export function parseBoardCount(raw) {
  const text = String(raw).trim();
  if (!/^\d+$/.test(text)) return null;
  const n = Number(text);
  return Number.isSafeInteger(n) && n >= 1 ? n : null;
}
