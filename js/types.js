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
 * @property {string} mouser - Mouser part number
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

// ── Python API return types (window.pywebview.api.*) ──

/**
 * Returned by fetch_lcsc_product(), fetch_digikey_product(),
 * fetch_mouser_product(), fetch_pololu_product().
 * @typedef {Object} DistributorProduct
 * @property {string} productCode - Distributor-specific identifier
 * @property {string} title - Product title
 * @property {string} manufacturer - Manufacturer name
 * @property {string} mpn - Manufacturer part number
 * @property {string} package - Package/footprint (may be empty)
 * @property {string} description - Product description
 * @property {number} stock - Quantity in stock at distributor
 * @property {PriceTier[]} prices - Price break tiers
 * @property {string} imageUrl - Product image URL
 * @property {string} pdfUrl - Datasheet PDF URL (may be empty)
 * @property {string} category - Product category
 * @property {string} subcategory - Product subcategory
 * @property {{name: string, value: string}[]} attributes - Part attributes
 * @property {string} provider - "lcsc"|"digikey"|"mouser"|"pololu"
 */

/**
 * @typedef {Object} PriceTier
 * @property {number} qty - Minimum order quantity for this tier
 * @property {number} price - Unit price at this tier
 */

/**
 * Returned by get_price_summary(). Keys are distributor names.
 * @typedef {Object<string, PriceSummaryEntry>} PriceSummary
 */

/**
 * @typedef {Object} PriceSummaryEntry
 * @property {number} latest_unit_price - Most recent observed price
 * @property {number} avg_unit_price - Average across all observations
 * @property {number} price_count - Number of observations
 * @property {string} last_observed - ISO timestamp of last observation
 * @property {number|string} moq - Minimum order quantity
 * @property {string} source - "live_fetch"|"import"|"manual"
 */

/**
 * Returned by list_generic_parts().
 * @typedef {Object} GenericPart
 * @property {string} generic_part_id - Unique identifier
 * @property {string} name - Display name
 * @property {string} part_type - "capacitor"|"resistor"|"inductor"|"other"
 * @property {Object} spec - Component spec (value, package, voltage, etc.)
 * @property {Object} strictness - Matching strictness config
 * @property {GenericPartMember[]} members - Member parts
 */

/**
 * @typedef {Object} GenericPartMember
 * @property {string} part_id - Inventory part key
 * @property {string} source - "auto"|"manual"
 * @property {number} preferred - 0 or 1
 * @property {number} quantity - Current stock quantity
 */

/**
 * Returned by resolve_bom_spec().
 * @typedef {Object} BomSpecResolution
 * @property {string} generic_part_id - Matched generic part ID
 * @property {string} generic_name - Generic part display name
 * @property {string} best_part_id - Recommended real part (preferred or highest qty)
 * @property {GenericPartMember[]} members - All matching members
 */

/**
 * Returned by extract_spec().
 * @typedef {Object} ComponentSpec
 * @property {string} type - "capacitor"|"resistor"|"inductor"|"other"
 * @property {string} package - Package/footprint
 * @property {number} [value] - Parsed numeric value (ohms, farads, henries)
 * @property {string} [value_display] - Formatted with SI prefix (e.g. "1kΩ")
 * @property {number} [voltage] - Voltage rating
 * @property {string} [tolerance] - e.g. "1%", "±10%"
 * @property {string} [dielectric] - Capacitor dielectric (C0G, X7R, etc.)
 */
