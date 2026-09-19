// @ts-check
/**
 * js/inventory/filter-chips-fields.js — Field descriptor for inventory filter chips.
 *
 * Builds the filterable-fields list for inventory items:
 *   - text: mpn, description, package, section
 *   - number: qty, unit_price, value (computed = qty × unit_price)
 *   - enum: distributor (derived from live inventory), section (derived),
 *           server (derived; present only in a merged view)
 *
 * Exports:
 *   buildInventoryFields(inventory)  → FieldDef[]
 *   extractInventoryField(item, key) → any   — field value extractor incl. "value" computed
 *   filterByPredicate(parts, ast)    → parts[] — apply matchesPredicate via field extractor
 */

import { matchesPredicate } from '../components/predicate-ui.js';
import { inferDistributor } from './inventory-logic.js';
import { sourceNames, serverOptions } from './inv-source-logic.js';

// ── Field definitions (static portion; options derived from live inventory) ──

/** @type {Array<{ key: string, label: string, type: string }>} */
const STATIC_FIELDS = [
  { key: 'mpn',         label: 'MPN',         type: 'text'   },
  { key: 'description', label: 'Description',  type: 'text'   },
  { key: 'package',     label: 'Package',      type: 'text'   },
  { key: 'qty',         label: 'Qty',          type: 'number' },
  { key: 'unit_price',  label: 'Unit Price',   type: 'number' },
  { key: 'value',       label: 'Value ($)',     type: 'number' },
  { key: 'distributor', label: 'Distributor',  type: 'enum'   },
  { key: 'section',     label: 'Section',      type: 'enum'   },
];

/* The merged view's provenance field. Kept OUT of STATIC_FIELDS because, unlike
   every field above, it does not exist in a single-server view: there, every
   row is from the one server the window is pointed at, so a `server` chip could
   only ever match everything or nothing. buildInventoryFields adds it exactly
   when the rows carry provenance. */
const SERVER_FIELD = { key: 'server', label: 'Server', type: 'enum' };

const DISTRIBUTOR_OPTIONS = ['lcsc', 'digikey', 'mouser', 'pololu', 'direct'];

/**
 * Build the complete filterable-fields descriptor from the live inventory.
 * Derives enum option lists from actual items so they stay current.
 *
 * @param {Array<Record<string, any>>} inventory
 * @returns {Array<{ key: string, label: string, type: string, options?: string[] }>}
 */
export function buildInventoryFields(inventory) {
  // Derive unique section names present in the current inventory
  /** @type {Set<string>} */
  const sectionSet = new Set();
  for (const item of inventory) {
    if (item.section) sectionSet.add(item.section);
  }
  const sectionOptions = [...sectionSet].sort();

  // Build field list with enum options injected
  const fields = STATIC_FIELDS.map((f) => {
    if (f.key === 'distributor') {
      return { ...f, options: DISTRIBUTOR_OPTIONS };
    }
    if (f.key === 'section') {
      return { ...f, options: sectionOptions };
    }
    return { ...f };
  });

  // Options come from the rows, not from the server roster, for the same reason
  // the section options do: the roster can name a server that contributed
  // nothing to this view (disabled, or unreachable), and an option that can only
  // ever match zero rows is a filter that looks broken.
  const servers = serverOptions(inventory);
  if (servers.length) fields.push({ ...SERVER_FIELD, options: servers });

  return fields;
}

/**
 * Extract the value of a named field from an inventory item.
 * Handles the computed "value" field (qty × unit_price) and the virtual
 * "distributor" field (derived via inferDistributor).
 *
 * @param {import('../types.js').InventoryItem} item
 * @param {string} key
 * @returns {any}
 */
export function extractInventoryField(item, key) {
  if (key === 'value') {
    const qty = Number(item.qty) || 0;
    const up  = Number(item.unit_price) || 0;
    return qty * up;
  }
  if (key === 'distributor') {
    return inferDistributor(item);
  }
  if (key === 'server') {
    // An ARRAY, not a name: a merged row can be stocked on two servers at once,
    // and picking one of them to report would make `server is shop` hide a part
    // that is, in fact, in the shop. matchesPredicate treats a set-valued field
    // as "any member matches" (js/components/predicate-ui.js).
    return sourceNames(item);
  }
  return item[key];
}

/**
 * Build a "flattened" item suitable for matchesPredicate by expanding computed
 * virtual fields into a plain object that matchesPredicate can look up by key.
 *
 * @param {import('../types.js').InventoryItem} item
 * @returns {Record<string, any>}
 */
function flattenForPredicate(item) {
  return Object.assign({}, item, {
    value: extractInventoryField(item, 'value'),
    distributor: extractInventoryField(item, 'distributor'),
    server: extractInventoryField(item, 'server'),
  });
}

/**
 * Filter an array of inventory items by a predicate AST.
 * Null / undefined ast → returns parts unchanged (no filter active).
 *
 * @param {Array<import('../types.js').InventoryItem>} parts
 * @param {any} ast  — GroupAst | null | undefined
 * @returns {Array<import('../types.js').InventoryItem>}
 */
export function filterByPredicate(parts, ast) {
  if (!ast || !('rules' in ast) || !ast.rules || ast.rules.length === 0) return parts;
  return parts.filter((item) => matchesPredicate(flattenForPredicate(item), ast));
}
