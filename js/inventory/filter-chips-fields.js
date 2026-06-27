// @ts-check
/**
 * js/inventory/filter-chips-fields.js — Field descriptor for inventory filter chips.
 *
 * Builds the filterable-fields list for inventory items:
 *   - text: mpn, description, package, section
 *   - number: qty, unit_price, value (computed = qty × unit_price)
 *   - enum: distributor (derived from live inventory), section (derived)
 *
 * Exports:
 *   buildInventoryFields(inventory)  → FieldDef[]
 *   extractInventoryField(item, key) → any   — field value extractor incl. "value" computed
 *   filterByPredicate(parts, ast)    → parts[] — apply matchesPredicate via field extractor
 */

import { matchesPredicate } from '../components/predicate-ui.js';
import { inferDistributor } from './inventory-logic.js';

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
  return STATIC_FIELDS.map((f) => {
    if (f.key === 'distributor') {
      return { ...f, options: DISTRIBUTOR_OPTIONS };
    }
    if (f.key === 'section') {
      return { ...f, options: sectionOptions };
    }
    return { ...f };
  });
}

/**
 * Extract the value of a named field from an inventory item.
 * Handles the computed "value" field (qty × unit_price) and the virtual
 * "distributor" field (derived via inferDistributor).
 *
 * @param {Record<string, any>} item
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
    return inferDistributor(/** @type {any} */ (item));
  }
  return item[key];
}

/**
 * Build a "flattened" item suitable for matchesPredicate by expanding computed
 * virtual fields into a plain object that matchesPredicate can look up by key.
 *
 * @param {Record<string, any>} item
 * @returns {Record<string, any>}
 */
function flattenForPredicate(item) {
  return Object.assign({}, item, {
    value: extractInventoryField(item, 'value'),
    distributor: extractInventoryField(item, 'distributor'),
  });
}

/**
 * Filter an array of inventory items by a predicate AST.
 * Null / undefined ast → returns parts unchanged (no filter active).
 *
 * @param {Array<Record<string, any>>} parts
 * @param {any} ast  — GroupAst | null | undefined
 * @returns {Array<Record<string, any>>}
 */
export function filterByPredicate(parts, ast) {
  if (!ast || !('rules' in ast) || !ast.rules || ast.rules.length === 0) return parts;
  return parts.filter((item) => matchesPredicate(flattenForPredicate(item), ast));
}
