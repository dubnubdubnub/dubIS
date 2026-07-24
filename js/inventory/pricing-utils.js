// @ts-check
/* pricing-utils.js — pure price-tier helpers shared by the Adjust + Price
   modals. Extracted from inventory-modals.js (Task 12 split). */

/** Pick the price-break tier matching a target quantity: the tier with the
 *  largest qty that is <= targetQty. Falls back to the lowest-qty tier when
 *  targetQty is missing/<=0 or no tier qualifies. Returns a tier or null. */
export function pickTier(prices, targetQty) {
  if (!Array.isArray(prices) || prices.length === 0) return null;
  const sorted = prices.slice().sort((a, b) => a.qty - b.qty);
  let chosen = sorted[0];
  if (typeof targetQty === "number" && targetQty > 0) {
    for (let i = 0; i < sorted.length; i++) {
      if (sorted[i].qty <= targetQty) chosen = sorted[i];
    }
  }
  return chosen;
}

/** Resolve a distributor row's price at a target quantity.
 *  Returns the chosen tier plus unit + extended (unit × qty) price,
 *  or all-null when there are no usable price tiers. */
export function rowPrice(prices, qty) {
  const tier = pickTier(prices, qty);
  if (!tier) return { tier: null, unitPrice: null, extPrice: null };
  return { tier, unitPrice: tier.price, extPrice: tier.price * qty };
}

/** Index of the cheapest row by unitPrice (ties → lowest index).
 *  Rows whose unitPrice is not a finite number are ignored. Returns -1
 *  when no row has a usable price. */
export function cheapestRow(rows) {
  let best = -1;
  let bestPrice = Infinity;
  for (let i = 0; i < rows.length; i++) {
    const p = rows[i] && rows[i].unitPrice;
    if (typeof p === "number" && isFinite(p) && p < bestPrice) {
      bestPrice = p;
      best = i;
    }
  }
  return best;
}
