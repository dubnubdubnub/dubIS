/* import-logic.js — Pure functions for import panel (no DOM, no store, no events) */

// Inventory field names that can be mapped to
export const TARGET_FIELDS = [
  "Skip",
  "LCSC Part Number",
  "Digikey Part Number",
  "Pololu Part Number",
  "Mouser Part Number",
  "Manufacture Part Number",
  "Manufacturer",
  "Quantity",
  "Description",
  "Package",
  "Unit Price($)",
  "Ext.Price($)",
  "RoHS",
  "Customer NO.",
];

export const PART_ID_FIELDS = ["LCSC Part Number", "Digikey Part Number", "Pololu Part Number", "Mouser Part Number", "Manufacture Part Number"];

export const PO_TEMPLATES = {
  generic: {
    label: "Generic",
    headers: [
      "Manufacture Part Number", "Manufacturer", "Description",
      "Package", "Quantity", "Unit Price($)",
    ],
  },
  lcsc: {
    label: "LCSC",
    headers: [
      "LCSC Part Number", "Manufacture Part Number", "Manufacturer",
      "Description", "Package", "Quantity", "Unit Price($)",
    ],
  },
  digikey: {
    label: "DigiKey",
    headers: [
      "Digikey Part Number", "Manufacture Part Number", "Manufacturer",
      "Description", "Package", "Quantity", "Unit Price($)",
    ],
  },
  pololu: {
    label: "Pololu",
    headers: [
      "Pololu Part Number", "Manufacture Part Number", "Manufacturer",
      "Description", "Package", "Quantity", "Unit Price($)",
    ],
  },
  mouser: {
    label: "Mouser",
    headers: [
      "Mouser Part Number", "Manufacture Part Number", "Manufacturer",
      "Description", "Package", "Quantity", "Unit Price($)",
    ],
  },
};

/**
 * Classify a row for validation (subtotal, warn, or ok).
 * @param {string[]} row - parsed CSV row values
 * @param {Object<number, string>} columnMapping - source column index -> target field name
 * @returns {"subtotal" | "warn" | "ok"}
 */
export function classifyRow(row, columnMapping) {
  const joined = row.join("").toLowerCase();
  if (joined.includes("subtotal") || joined.includes("total:")) return "subtotal";

  // Check part ID: any column mapped to a part ID field has a value
  const hasPart = PART_ID_FIELDS.some(f => {
    const colIdx = Object.keys(columnMapping).find(k => columnMapping[k] === f);
    return colIdx !== undefined && (row[parseInt(colIdx)] || "").trim() !== "";
  });

  // Check quantity
  const qtyField = Object.keys(columnMapping).find(k => columnMapping[k] === "Quantity");
  let qtyOk = true;
  if (qtyField !== undefined) {
    const raw = (row[parseInt(qtyField)] || "").replace(/,/g, "").replace(/"/g, "").trim();
    const parsed = parseInt(raw, 10);
    if (isNaN(parsed) || parsed <= 0) qtyOk = false;
  }

  if (!hasPart || !qtyOk) return "warn";
  return "ok";
}

/**
 * Count rows classified as warnings or subtotals.
 * @param {string[][]} rows
 * @param {Object<number, string>} columnMapping
 * @returns {number}
 */
export function countWarnings(rows, columnMapping) {
  let warns = 0;
  rows.forEach(row => {
    const cls = classifyRow(row, columnMapping);
    if (cls === "warn" || cls === "subtotal") warns++;
  });
  return warns;
}

/**
 * Transform parsed CSV rows into inventory format ready for import.
 * @param {string[][]} parsedRows
 * @param {Object<number, string>} columnMapping - source column index -> target field name
 * @param {string[]} _targetFields - unused, kept for API symmetry
 * @returns {Object[]} array of inventory row objects
 */
export function transformImportRows(parsedRows, columnMapping, _targetFields) {
  const invRows = [];
  parsedRows.forEach(row => {
    const invRow = {};
    for (const [colIdx, targetField] of Object.entries(columnMapping)) {
      if (targetField === "Skip") continue;
      let val = (row[parseInt(colIdx)] || "").trim();

      // Clean up values
      if (targetField === "Quantity") {
        val = val.replace(/,/g, "").replace(/"/g, "");
        const parsed = parseInt(val, 10);
        val = isNaN(parsed) ? "0" : String(parsed);
      }
      if (targetField === "Unit Price($)" || targetField === "Ext.Price($)") {
        val = val.replace(/[$,]/g, "");
        const parsed = parseFloat(val);
        val = isNaN(parsed) ? "" : parsed.toFixed(2);
      }

      invRow[targetField] = val;
    }
    invRows.push(invRow);
  });
  return invRows;
}

/**
 * Check if import data has at least one row with a part ID field populated.
 * @param {Object[]} rows - transformed inventory rows
 * @param {string[]} partIdFields
 * @returns {boolean}
 */
export function validateImportData(rows, partIdFields) {
  if (!rows || rows.length === 0) return false;
  return rows.some(row =>
    partIdFields.some(f => (row[f] || "").trim() !== "")
  );
}
