// @ts-check
/**
 * Run the bulk "fetch missing descriptions" action.
 * @param {{api:Function,onInventoryUpdated:Function,showToast:Function}} deps
 */
export async function runFetchMissingDescriptions({ api, onInventoryUpdated, showToast }) {
  const res = await api('fetch_missing_descriptions');
  if (!res) return; // api() already toasted the error
  // unwrap: "detail" (js/api-map.js) — domain/inventory.py's
  // fetch_missing_descriptions already returns `{inventory, summary}` (it
  // rebuilds after writing descriptions), and server/routes/inventory_mut.py
  // passes that facade return straight through as `detail`, so `res` here
  // already IS that shape — no separate round trip needed for the fresh
  // inventory.
  const { inventory, summary } = res;
  if (Array.isArray(inventory)) onInventoryUpdated(inventory);
  const s = summary || { updated: 0, failed: 0 };
  if (!s.updated) {
    if (s.failed) {
      showToast('Could not fetch ' + s.failed + ' description' + (s.failed === 1 ? '' : 's'));
    } else {
      showToast('No missing descriptions to fetch');
    }
    return;
  }
  let msg = 'Fetched ' + s.updated + ' description' + (s.updated === 1 ? '' : 's');
  if (s.failed) msg += ', ' + s.failed + ' failed';
  showToast(msg);
}
