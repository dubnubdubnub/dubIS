/**
 * types.js — JSDoc type definitions for shared data shapes.
 * No runtime code — just @typedef comments for editor tooling and grep-ability.
 */

/**
 * @typedef {Object} InventoryItem
 * @property {string} section - Category section (e.g. "Capacitors > Ceramic")
 * @property {string} lcsc - LCSC part number (e.g. "C123456")
 * @property {string} mpn - Manufacturer part number
 * @property {string} digikey - Digikey part number
 * @property {string} pololu - Pololu SKU number
 * @property {string} manufacturer - Manufacturer name
 * @property {string} package - Package/footprint (e.g. "0402")
 * @property {string} description - Part description
 * @property {number} qty - Quantity in stock
 * @property {number} unit_price - Unit price in dollars
 * @property {number} ext_price - Extended price (qty * unit_price)
 */

/**
 * @typedef {Object} BomAggregatedRow
 * @property {string} lcsc - LCSC part number from BOM
 * @property {string} mpn - MPN from BOM
 * @property {string} value - Component value (e.g. "100nF")
 * @property {string} desc - Description from BOM
 * @property {number} qty - Total quantity needed
 * @property {string} refs - Comma-separated designators (e.g. "R1,R2,R3")
 * @property {boolean} dnp - Do Not Place flag
 */

/**
 * @typedef {Object} BomMatchResult
 * @property {BomAggregatedRow} bom - The aggregated BOM row
 * @property {InventoryItem|null} inv - Matched inventory item, or null
 * @property {InventoryItem[]} alts - Alternative inventory items
 * @property {string|null} matchType - "exact"|"confirmed"|"manual"|"value"|"fuzzy"|"prefix"|null
 * @property {string} status - Raw status from matchBOM
 * @property {number} effectiveQty - Qty after multiplier
 * @property {string} effectiveStatus - "ok"|"short"|"possible"|"missing"|"manual"|"confirmed"|"dnp"|"manual-short"|"confirmed-short"
 * @property {number} [altQty] - Total qty across alts
 * @property {number} [combinedQty] - inv.qty + altQty
 * @property {boolean} [coveredByAlts] - Whether alts cover the shortfall
 */

/**
 * @typedef {Object} ManualLink
 * @property {string} bomKey - Key identifying the BOM row
 * @property {string} invPartKey - Key identifying the inventory part
 */

/**
 * @typedef {Object} ConfirmedMatch
 * @property {string} bomKey - Key identifying the BOM row
 * @property {string} invPartKey - Key identifying the inventory part
 */

/**
 * @typedef {Object} LinkState
 * @property {ManualLink[]} manualLinks - User-created manual links
 * @property {ConfirmedMatch[]} confirmedMatches - User-confirmed matches
 * @property {boolean} linkingMode - Whether linking mode is active
 * @property {InventoryItem|null} linkingInvItem - Inventory item being linked (forward mode)
 * @property {BomMatchResult|null} linkingBomRow - BOM row being linked (reverse mode)
 */

/**
 * @typedef {Object} Preferences
 * @property {Object<string, number>} thresholds - Stock value thresholds per section
 * @property {string} [lastBomDir] - Last directory used for BOM file open
 * @property {string} [lastImportDir] - Last directory used for purchase import
 * @property {string} [lastBomFile] - Last BOM file path
 */
