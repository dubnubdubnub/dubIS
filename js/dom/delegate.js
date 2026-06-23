// @ts-check
/**
 * js/dom/delegate.js — Event delegation with clean teardown.
 *
 * Exports:
 *   on(root, type, selector, handler)  → remover function
 *   bind(root, map)                    → remover function
 *
 * Both `on` and `bind` use bubble-phase delegation: a single listener is
 * attached to `root`; on each event, `event.target.closest(selector)` is
 * tested and the handler is called only when the match is a descendant of root.
 * This naturally handles nodes added after binding (true delegation).
 */

/**
 * @callback DelegateHandler
 * @param {Event} event
 * @param {Element} matchedEl
 * @returns {void}
 */

/**
 * Attach a single delegated event listener on `root` for `type`.
 * When an event bubbles up, `event.target.closest(selector)` is checked;
 * if it is within `root` (but not root itself acting as source is fine),
 * `handler(event, matchedEl)` is called.
 *
 * @param {Element} root
 * @param {string} type
 * @param {string} selector
 * @param {DelegateHandler} handler
 * @returns {() => void} — call to remove the listener
 */
export function on(root, type, selector, handler) {
  /**
   * @param {Event} event
   */
  function listener(event) {
    const target = /** @type {Element} */ (event.target);
    if (!(target instanceof Element)) return;
    const matched = target.closest(selector);
    if (!matched) return;
    // The matched element must be a descendant of (or equal to) root,
    // but only fire if root actually contains it (not a match outside root).
    if (!root.contains(matched)) return;
    handler(event, matched);
  }

  root.addEventListener(type, listener);

  return function remove() {
    root.removeEventListener(type, listener);
  };
}

/**
 * Attach multiple delegated listeners from a map of `"type selector"` → handler.
 * The selector is everything after the first space in the key.
 *
 * @param {Element} root
 * @param {Record<string, DelegateHandler>} map
 * @returns {() => void} — call to remove all listeners
 */
export function bind(root, map) {
  const removers = [];

  for (const [key, handler] of Object.entries(map)) {
    const spaceIdx = key.indexOf(' ');
    if (spaceIdx === -1) {
      throw new Error(`bind: key "${key}" must be in the form "<type> <selector>"`);
    }
    const type = key.slice(0, spaceIdx);
    const selector = key.slice(spaceIdx + 1);
    removers.push(on(root, type, selector, handler));
  }

  return function removeAll() {
    for (const remove of removers) remove();
  };
}
