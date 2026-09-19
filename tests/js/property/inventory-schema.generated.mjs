// AUTO-GENERATED — do not edit by hand.
// Source of truth: domain/schema.py :: INVENTORY_FIELDS
// Regenerate: python scripts/gen-property-schema.py
//
// The field table tests/js/property/arbitraries.mjs builds its inventory-record
// arbitrary from. `jsType` splits the schema's single `number` into `int` and
// `float` using the type of the declared default — see the script's docstring.

export const INVENTORY_FIELDS = /** @type {const} */ ([
  {
    "key": "section",
    "tsType": "string",
    "jsType": "string",
    "default": ""
  },
  {
    "key": "lcsc",
    "tsType": "string",
    "jsType": "string",
    "default": ""
  },
  {
    "key": "mpn",
    "tsType": "string",
    "jsType": "string",
    "default": ""
  },
  {
    "key": "digikey",
    "tsType": "string",
    "jsType": "string",
    "default": ""
  },
  {
    "key": "pololu",
    "tsType": "string",
    "jsType": "string",
    "default": ""
  },
  {
    "key": "mouser",
    "tsType": "string",
    "jsType": "string",
    "default": ""
  },
  {
    "key": "manufacturer",
    "tsType": "string",
    "jsType": "string",
    "default": ""
  },
  {
    "key": "package",
    "tsType": "string",
    "jsType": "string",
    "default": ""
  },
  {
    "key": "description",
    "tsType": "string",
    "jsType": "string",
    "default": ""
  },
  {
    "key": "qty",
    "tsType": "number",
    "jsType": "int",
    "default": 0
  },
  {
    "key": "unit_price",
    "tsType": "number",
    "jsType": "float",
    "default": 0.0
  },
  {
    "key": "ext_price",
    "tsType": "number",
    "jsType": "float",
    "default": 0.0
  },
  {
    "key": "primary_vendor_id",
    "tsType": "string",
    "jsType": "string",
    "default": ""
  },
  {
    "key": "po_history",
    "tsType": "string[]",
    "jsType": "string[]",
    "default": []
  }
]);

export const FIELD_KEYS = INVENTORY_FIELDS.map((f) => f.key);
