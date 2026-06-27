// AUTO-GENERATED — do not edit by hand.
// Source of truth: domain/schema.py :: INVENTORY_FIELDS + PartHistoryEntry
// Regenerate: python scripts/gen-inventory-types.py

export interface InventoryItem {
  section: string;
  lcsc: string;
  mpn: string;
  digikey: string;
  pololu: string;
  mouser: string;
  manufacturer: string;
  package: string;
  description: string;
  qty: number;
  unit_price: number;
  ext_price: number;
  primary_vendor_id: string;
  po_history: string[];
}

export interface PartHistoryEntry {
  timestamp: string;
  kind: string;
  qty_delta: number;
  source: string;
  note: string;
}
