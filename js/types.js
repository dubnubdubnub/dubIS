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

/**
 * @typedef {Object} KicadProject
 * @property {string} name - Board name
 * @property {string} kicad_pro - .kicad_pro filename
 * @property {string} last_scan - ISO timestamp of last scan
 */

/**
 * @typedef {Object} KicadPart
 * @property {string} ref - Reference designator (e.g. "R1")
 * @property {string} value - Component value (e.g. "10k")
 * @property {string} footprint - Simplified footprint name (e.g. "R_0402")
 * @property {string} footprint_full - Full KiCad footprint ref (e.g. "Resistor_SMD:R_0402_1005Metric")
 * @property {string} lcsc - LCSC part number
 * @property {string} mpn - Manufacturer part number
 * @property {boolean} dnp - Do Not Place flag
 */

/**
 * @typedef {Object} FootprintPad
 * @property {string} name - Pad name/number
 * @property {number} x - X position in mm
 * @property {number} y - Y position in mm
 * @property {number} width - Pad width in mm
 * @property {number} height - Pad height in mm
 * @property {number} rotation - Pad rotation in degrees
 * @property {number} roundness - 0=rect, 50=oval, 100=circle
 */

/**
 * @typedef {Object} FootprintData
 * @property {number} body_width - Component body width in mm
 * @property {number} body_height - Component body height in mm
 * @property {FootprintPad[]} pads - Array of pads
 */

/**
 * @typedef {Object} OpenpnpPartMeta
 * @property {string} openpnp_id - OpenPnP part ID
 * @property {string} package_id - OpenPnP package ID
 * @property {number} height - Part height in mm
 * @property {number} speed - Speed multiplier
 * @property {string[]} nozzle_tips - Compatible nozzle tip IDs
 * @property {FootprintData} [footprint] - Footprint data
 * @property {string} [footprint_source] - "easyeda"|"kicad"|"manual"
 * @property {string} [footprint_fetched] - ISO timestamp
 */
