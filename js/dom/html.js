// @ts-check
/**
 * js/dom/html.js — Tiny tagged-template DOM renderer.
 *
 * Exports:
 *   html(strings, ...values)  → DocumentFragment
 *   raw(s)                    → { __raw: string } marker (bypasses escaping)
 *   el(tag, attrs, ...children) → HTMLElement
 *   escapeHtml(s)             → string (escapes & < > " ')
 */

/**
 * Escape a string for safe HTML insertion. The result is safe in element text
 * content and in *quoted* attribute values (e.g. `title="${val}"`). It is NOT
 * safe for unquoted attribute interpolation (e.g. `title=${val}`) — always
 * quote the attribute. Stricter than ui-helpers.escHtml (which omits quotes
 * because it uses the browser DOM path and misses them).
 *
 * @param {any} s
 * @returns {string}
 */
export function escapeHtml(s) {
  if (s === null || s === undefined) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/**
 * Marker object produced by raw().  The `__raw` property holds trusted HTML
 * that will be inserted verbatim (no escaping).
 * @typedef {{ __raw: string }} RawMarker
 */

/**
 * Wrap trusted HTML so that `html` inserts it verbatim.
 * @param {any} s
 * @returns {RawMarker}
 */
export function raw(s) {
  return { __raw: String(s) };
}

/**
 * @param {any} v
 * @returns {v is RawMarker}
 */
function isRaw(v) {
  return v !== null && typeof v === 'object' && '__raw' in v;
}

/** Unique placeholder comment prefix */
const PLACEHOLDER_PREFIX = '⟦node:';

/**
 * Convert a single interpolated value to an HTML string fragment, registering
 * live Nodes in the nodeMap so they can be spliced in later.
 *
 * @param {any} v
 * @param {Map<number, Node>} nodeMap
 * @param {number[]} counter  — single-element array used as a mutable counter ref
 * @returns {string}
 */
function valueToHtml(v, nodeMap, counter) {
  if (v === null || v === undefined || v === false || v === true) return '';
  if (isRaw(v)) return v.__raw;
  if (Array.isArray(v)) {
    return v.map((item) => valueToHtml(item, nodeMap, counter)).join('');
  }
  if (v instanceof Node) {
    const id = counter[0]++;
    nodeMap.set(id, v);
    return `<!--${PLACEHOLDER_PREFIX}${id}⟧-->`;
  }
  // string or number
  return escapeHtml(v);
}

/**
 * Tagged template literal that returns a DocumentFragment.
 * Strings/numbers → escaped; Nodes/Fragments → inserted by identity;
 * Arrays → each item by the same rules; null/undefined/false/true → nothing;
 * raw() → inserted verbatim.
 *
 * @param {TemplateStringsArray} strings
 * @param {...any} values
 * @returns {DocumentFragment}
 */
export function html(strings, ...values) {
  /** @type {Map<number, Node>} */
  const nodeMap = new Map();
  const counter = [0];

  let markup = '';
  for (let i = 0; i < strings.length; i++) {
    markup += strings[i];
    if (i < values.length) {
      markup += valueToHtml(values[i], nodeMap, counter);
    }
  }

  const template = document.createElement('template');
  template.innerHTML = markup;
  const frag = template.content;

  if (nodeMap.size > 0) {
    // Walk the content tree replacing placeholder comments with the real nodes.
    // We collect them first to avoid mutation-during-iteration.
    /** @type {Comment[]} */
    const comments = [];
    const walker = document.createTreeWalker(frag, NodeFilter.SHOW_COMMENT);
    let node;
    while ((node = walker.nextNode())) {
      comments.push(/** @type {Comment} */ (node));
    }
    for (const comment of comments) {
      const text = comment.data;
      const match = text.match(/^⟦node:(\d+)⟧$/);
      if (match) {
        const realNode = nodeMap.get(Number(match[1]));
        if (realNode) {
          comment.parentNode.replaceChild(realNode, comment);
        }
      }
    }
  }

  return frag;
}

/**
 * Create a plain HTMLElement with optional attributes and children.
 *
 * attrs supports:
 *   class, id, style  → setAttribute
 *   dataset: { k: v } → element.dataset
 *   on: { event: fn } → addEventListener
 *   any other key     → setAttribute (skip null/undefined/false; true → present)
 *
 * children: strings → text nodes (NOT HTML-parsed), numbers → text nodes,
 *           Nodes → appended directly, Arrays → recursed.
 *
 * @param {string} tag
 * @param {Record<string, any>|null} [attrs]
 * @param {...(string|number|Node|Array)} children
 * @returns {HTMLElement}
 */
export function el(tag, attrs, ...children) {
  const element = document.createElement(tag);

  if (attrs) {
    for (const [key, val] of Object.entries(attrs)) {
      if (key === 'on') {
        if (val && typeof val === 'object') {
          for (const [event, fn] of Object.entries(val)) {
            element.addEventListener(event, fn);
          }
        }
      } else if (key === 'dataset') {
        if (val && typeof val === 'object') {
          for (const [k, v] of Object.entries(val)) {
            element.dataset[k] = String(v);
          }
        }
      } else {
        if (val === null || val === undefined || val === false) continue;
        if (val === true) {
          element.setAttribute(key, '');
        } else {
          element.setAttribute(key, String(val));
        }
      }
    }
  }

  appendChildren(element, children);
  return element;
}

/**
 * Recursively append children to a parent element.
 * Strings/numbers → text nodes; Nodes → appended; Arrays → recursed.
 *
 * @param {HTMLElement} parent
 * @param {Array<string|number|Node|Array>} children
 */
function appendChildren(parent, children) {
  for (const child of children) {
    if (child === null || child === undefined) continue;
    if (Array.isArray(child)) {
      appendChildren(parent, child);
    } else if (child instanceof Node) {
      parent.appendChild(child);
    } else {
      // string or number → text node (never parsed as HTML)
      parent.appendChild(document.createTextNode(String(child)));
    }
  }
}
