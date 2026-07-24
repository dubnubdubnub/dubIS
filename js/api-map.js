// AUTO-GENERATED — do not edit by hand.
// Source of truth: docs/openapi-v1.json
// Regenerate: python scripts/gen-api-client.py

export const API_MAP = {
  "add_generic_member": {
    "argOrder": [
      "generic_part_id",
      "part_id"
    ],
    "bodyParams": [
      "part_id"
    ],
    "mutating": true,
    "path": "/v1/generic-parts/{generic_part_id}/members",
    "pathParams": [
      "generic_part_id"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "detail",
    "verb": "POST"
  },
  "adjust_part": {
    "argOrder": [
      "adj_type",
      "part_key",
      "quantity",
      "note",
      "source"
    ],
    "bodyParams": [
      "adj_type",
      "note",
      "quantity",
      "source"
    ],
    "mutating": true,
    "path": "/v1/parts/{part_key}/adjust",
    "pathParams": [
      "part_key"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "detail",
    "verb": "POST"
  },
  "check_digikey_session": {
    "argOrder": [],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/distributors/digikey/session",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "GET"
  },
  "clear_mouser_api_key": {
    "argOrder": [],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/distributors/mouser/key",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "DELETE"
  },
  "consume_bom": {
    "argOrder": [
      "matches",
      "board_qty",
      "bom_name",
      "note",
      "source"
    ],
    "bodyParams": [
      "board_qty",
      "bom_name",
      "matches",
      "note",
      "source"
    ],
    "mutating": true,
    "path": "/v1/bom/consume",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "detail",
    "verb": "POST"
  },
  "create_generic_part": {
    "argOrder": [
      "name",
      "part_type",
      "spec",
      "strictness"
    ],
    "bodyParams": [
      "name",
      "part_type",
      "spec",
      "strictness"
    ],
    "mutating": true,
    "path": "/v1/generic-parts",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "detail",
    "verb": "POST"
  },
  "create_purchase_order_with_items": {
    "argOrder": [
      "vendor_id",
      "source_file_b64",
      "source_file_name",
      "purchase_date",
      "notes",
      "line_items"
    ],
    "bodyParams": [
      "line_items",
      "notes",
      "purchase_date",
      "source_file_b64",
      "source_file_name",
      "vendor_id"
    ],
    "mutating": true,
    "path": "/v1/purchase-orders",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "detail",
    "verb": "POST"
  },
  "create_saved_search": {
    "argOrder": [
      "generic_part_id",
      "name",
      "tag_state",
      "search_text",
      "frozen_members"
    ],
    "bodyParams": [
      "frozen_members",
      "name",
      "search_text",
      "tag_state"
    ],
    "mutating": false,
    "path": "/v1/generic-parts/{generic_part_id}/saved-searches",
    "pathParams": [
      "generic_part_id"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "detail",
    "verb": "POST"
  },
  "delete_last_purchase_order": {
    "argOrder": [],
    "bodyParams": [],
    "mutating": true,
    "path": "/v1/purchase-orders/last",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "detail",
    "verb": "DELETE"
  },
  "delete_part": {
    "argOrder": [
      "part_key"
    ],
    "bodyParams": [],
    "mutating": true,
    "path": "/v1/parts/{part_key}",
    "pathParams": [
      "part_key"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "detail",
    "verb": "DELETE"
  },
  "delete_purchase_order": {
    "argOrder": [
      "po_id"
    ],
    "bodyParams": [],
    "mutating": true,
    "path": "/v1/purchase-orders/{po_id}",
    "pathParams": [
      "po_id"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "detail",
    "verb": "DELETE"
  },
  "delete_saved_search": {
    "argOrder": [
      "search_id"
    ],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/saved-searches/{search_id}",
    "pathParams": [
      "search_id"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "detail",
    "verb": "DELETE"
  },
  "delete_vendor": {
    "argOrder": [
      "vendor_id"
    ],
    "bodyParams": [],
    "mutating": true,
    "path": "/v1/vendors/{vendor_id}",
    "pathParams": [
      "vendor_id"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "detail",
    "verb": "DELETE"
  },
  "detect_columns": {
    "argOrder": [
      "headers"
    ],
    "bodyParams": [
      "headers"
    ],
    "mutating": false,
    "path": "/v1/import/detect-columns",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "POST"
  },
  "exclude_generic_member": {
    "argOrder": [
      "generic_part_id",
      "part_id"
    ],
    "bodyParams": [],
    "mutating": true,
    "path": "/v1/generic-parts/{generic_part_id}/members/{part_id}/exclude",
    "pathParams": [
      "generic_part_id",
      "part_id"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "detail",
    "verb": "POST"
  },
  "extract_spec": {
    "argOrder": [
      "part_key"
    ],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/parts/{part_key}/spec",
    "pathParams": [
      "part_key"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "spec",
    "verb": "GET"
  },
  "extract_spec_from_value": {
    "argOrder": [
      "part_type",
      "value_str",
      "package_str"
    ],
    "bodyParams": [
      "package_str",
      "part_type",
      "value_str"
    ],
    "mutating": false,
    "path": "/v1/spec/extract",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "POST"
  },
  "fetch_digikey_product": {
    "argOrder": [
      "code"
    ],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/distributors/digikey/product/{code}",
    "pathParams": [
      "code"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "GET"
  },
  "fetch_distributor_product": {
    "argOrder": [
      "name",
      "code"
    ],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/distributors/{name}/product/{code}",
    "pathParams": [
      "name",
      "code"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "GET"
  },
  "fetch_favicon": {
    "argOrder": [
      "url"
    ],
    "bodyParams": [
      "url"
    ],
    "mutating": false,
    "path": "/v1/vendors/favicon",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "path",
    "verb": "POST"
  },
  "fetch_lcsc_product": {
    "argOrder": [
      "code"
    ],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/distributors/lcsc/product/{code}",
    "pathParams": [
      "code"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "GET"
  },
  "fetch_missing_descriptions": {
    "argOrder": [],
    "bodyParams": [],
    "mutating": true,
    "path": "/v1/parts/fetch-missing-descriptions",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "detail",
    "verb": "POST"
  },
  "fetch_mouser_product": {
    "argOrder": [
      "code"
    ],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/distributors/mouser/product/{code}",
    "pathParams": [
      "code"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "GET"
  },
  "fetch_pololu_product": {
    "argOrder": [
      "code"
    ],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/distributors/pololu/product/{code}",
    "pathParams": [
      "code"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "GET"
  },
  "get_digikey_login_status": {
    "argOrder": [],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/distributors/digikey/session",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "GET"
  },
  "get_digikey_session": {
    "argOrder": [],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/distributors/digikey/session",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "GET"
  },
  "get_feeder": {
    "argOrder": [
      "tag_id"
    ],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/feeders/{tag_id}",
    "pathParams": [
      "tag_id"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "GET"
  },
  "get_generic_group_names": {
    "argOrder": [
      "part_key"
    ],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/parts/{part_key}/groups",
    "pathParams": [
      "part_key"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "groups",
    "verb": "GET"
  },
  "get_last_po_quantity": {
    "argOrder": [
      "part_key"
    ],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/parts/{part_key}/last-po-quantity",
    "pathParams": [
      "part_key"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "quantity",
    "verb": "GET"
  },
  "get_mouser_api_key_status": {
    "argOrder": [],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/distributors/mouser/key",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "GET"
  },
  "get_part_history": {
    "argOrder": [
      "part_key"
    ],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/parts/{part_key}/history",
    "pathParams": [
      "part_key"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "GET"
  },
  "get_po_source": {
    "argOrder": [
      "po_id"
    ],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/purchase-orders/{po_id}/source",
    "pathParams": [
      "po_id"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "GET"
  },
  "get_po_source_preview": {
    "argOrder": [
      "po_id"
    ],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/purchase-orders/{po_id}/preview",
    "pathParams": [
      "po_id"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "GET"
  },
  "get_po_with_items": {
    "argOrder": [
      "po_id"
    ],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/purchase-orders/{po_id}",
    "pathParams": [
      "po_id"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "GET"
  },
  "get_price_summary": {
    "argOrder": [
      "part_key"
    ],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/parts/{part_key}/prices",
    "pathParams": [
      "part_key"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "GET"
  },
  "get_sourced_distributors": {
    "argOrder": [
      "part_key"
    ],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/parts/{part_key}/distributors",
    "pathParams": [
      "part_key"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "GET"
  },
  "get_warnings": {
    "argOrder": [],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/warnings",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "GET"
  },
  "has_purchase_history": {
    "argOrder": [
      "part_key"
    ],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/parts/{part_key}/purchase-history",
    "pathParams": [
      "part_key"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "has_purchase_history",
    "verb": "GET"
  },
  "import_purchases": {
    "argOrder": [
      "rows"
    ],
    "bodyParams": [
      "rows"
    ],
    "mutating": true,
    "path": "/v1/purchases/import",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "detail",
    "verb": "POST"
  },
  "list_feeders": {
    "argOrder": [],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/feeders",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "GET"
  },
  "list_generic_parts": {
    "argOrder": [],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/generic-parts",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "GET"
  },
  "list_parts": {
    "argOrder": [],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/parts",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "inventory",
    "verb": "GET"
  },
  "list_purchase_orders": {
    "argOrder": [],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/purchase-orders",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "GET"
  },
  "list_saved_searches": {
    "argOrder": [
      "generic_part_id"
    ],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/generic-parts/{generic_part_id}/saved-searches",
    "pathParams": [
      "generic_part_id"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "GET"
  },
  "list_vendors": {
    "argOrder": [],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/vendors",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "GET"
  },
  "load_feeder_reel": {
    "argOrder": [
      "tag_id",
      "part_key",
      "qty",
      "tape_width_mm"
    ],
    "bodyParams": [
      "part_key",
      "qty",
      "tape_width_mm"
    ],
    "mutating": false,
    "path": "/v1/feeders/{tag_id}/load",
    "pathParams": [
      "tag_id"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "POST"
  },
  "load_preferences": {
    "argOrder": [],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/preferences",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "GET"
  },
  "logout_digikey": {
    "argOrder": [],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/distributors/digikey/session",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "DELETE"
  },
  "match_part": {
    "argOrder": [
      "mpn",
      "manufacturer"
    ],
    "bodyParams": [
      "manufacturer",
      "mpn"
    ],
    "mutating": false,
    "path": "/v1/import/match-part",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "POST"
  },
  "merge_vendors": {
    "argOrder": [
      "src_id",
      "dst_id"
    ],
    "bodyParams": [
      "dst_id",
      "src_id"
    ],
    "mutating": true,
    "path": "/v1/vendors/merge",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "detail",
    "verb": "POST"
  },
  "ocr_engine_available": {
    "argOrder": [],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/import/ocr/available",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "available",
    "verb": "GET"
  },
  "ocr_overlay": {
    "argOrder": [
      "file_b64",
      "file_name",
      "template"
    ],
    "bodyParams": [
      "file_b64",
      "file_name",
      "template"
    ],
    "mutating": false,
    "path": "/v1/import/ocr",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "POST"
  },
  "ocr_overlay_b64": {
    "argOrder": [
      "file_b64",
      "file_name",
      "template"
    ],
    "bodyParams": [
      "file_b64",
      "file_name",
      "template"
    ],
    "mutating": false,
    "path": "/v1/import/ocr",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "POST"
  },
  "parse_import_source": {
    "argOrder": [
      "file_b64",
      "file_name",
      "path",
      "template"
    ],
    "bodyParams": [
      "file_b64",
      "file_name",
      "path",
      "template"
    ],
    "mutating": false,
    "path": "/v1/import/parse",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "POST"
  },
  "parse_source_file": {
    "argOrder": [
      "path",
      "template"
    ],
    "bodyParams": [
      "path",
      "template"
    ],
    "mutating": false,
    "path": "/v1/import/parse",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "POST"
  },
  "parse_source_file_b64": {
    "argOrder": [
      "file_b64",
      "file_name",
      "template"
    ],
    "bodyParams": [
      "file_b64",
      "file_name",
      "template"
    ],
    "mutating": false,
    "path": "/v1/import/parse",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "POST"
  },
  "pnp_consume": {
    "argOrder": [
      "part_id",
      "qty"
    ],
    "bodyParams": [
      "part_id",
      "qty"
    ],
    "mutating": false,
    "path": "/v1/pnp/consume",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "POST"
  },
  "rebuild_inventory": {
    "argOrder": [],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/parts",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "inventory",
    "verb": "GET"
  },
  "record_fetched_prices": {
    "argOrder": [
      "part_key",
      "distributor",
      "price_tiers"
    ],
    "bodyParams": [
      "distributor",
      "price_tiers"
    ],
    "mutating": true,
    "path": "/v1/parts/{part_key}/fetched-prices",
    "pathParams": [
      "part_key"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "detail",
    "verb": "POST"
  },
  "register_feeder": {
    "argOrder": [
      "tag_id",
      "feeder_type"
    ],
    "bodyParams": [
      "feeder_type"
    ],
    "mutating": false,
    "path": "/v1/feeders/{tag_id}/register",
    "pathParams": [
      "tag_id"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "POST"
  },
  "remove_generic_member": {
    "argOrder": [
      "generic_part_id",
      "part_id"
    ],
    "bodyParams": [],
    "mutating": true,
    "path": "/v1/generic-parts/{generic_part_id}/members/{part_id}",
    "pathParams": [
      "generic_part_id",
      "part_id"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "detail",
    "verb": "DELETE"
  },
  "remove_last_adjustments": {
    "argOrder": [
      "count"
    ],
    "bodyParams": [],
    "mutating": true,
    "path": "/v1/adjustments/last",
    "pathParams": [],
    "queryParams": [
      "count"
    ],
    "rawBody": false,
    "unwrap": "detail",
    "verb": "DELETE"
  },
  "remove_last_purchases": {
    "argOrder": [
      "count"
    ],
    "bodyParams": [],
    "mutating": true,
    "path": "/v1/purchases/last",
    "pathParams": [],
    "queryParams": [
      "count"
    ],
    "rawBody": false,
    "unwrap": "detail",
    "verb": "DELETE"
  },
  "resolve_bom_spec": {
    "argOrder": [
      "part_type",
      "value",
      "package"
    ],
    "bodyParams": [
      "package",
      "part_type",
      "value"
    ],
    "mutating": false,
    "path": "/v1/bom/resolve-spec",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "match",
    "verb": "POST"
  },
  "rollback_source": {
    "argOrder": [
      "source"
    ],
    "bodyParams": [],
    "mutating": true,
    "path": "/v1/adjustments/by-source/{source}",
    "pathParams": [
      "source"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "detail",
    "verb": "DELETE"
  },
  "save_preferences": {
    "argOrder": [
      "prefs"
    ],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/preferences",
    "pathParams": [],
    "queryParams": [],
    "rawBody": true,
    "unwrap": null,
    "verb": "PUT"
  },
  "set_mouser_api_key": {
    "argOrder": [
      "key"
    ],
    "bodyParams": [
      "key"
    ],
    "mutating": false,
    "path": "/v1/distributors/mouser/key",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "PUT"
  },
  "set_preferred_member": {
    "argOrder": [
      "generic_part_id",
      "part_id"
    ],
    "bodyParams": [],
    "mutating": true,
    "path": "/v1/generic-parts/{generic_part_id}/members/{part_id}/preferred",
    "pathParams": [
      "generic_part_id",
      "part_id"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "detail",
    "verb": "PUT"
  },
  "start_scan_session": {
    "argOrder": [
      "template"
    ],
    "bodyParams": [
      "template"
    ],
    "mutating": false,
    "path": "/v1/scan/sessions",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "POST"
  },
  "sync_digikey_cookies": {
    "argOrder": [],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/distributors/digikey/cookies/sync",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "POST"
  },
  "unload_feeder": {
    "argOrder": [
      "tag_id"
    ],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/feeders/{tag_id}/unload",
    "pathParams": [
      "tag_id"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "POST"
  },
  "update_generic_part": {
    "argOrder": [
      "generic_part_id",
      "name",
      "spec",
      "strictness"
    ],
    "bodyParams": [
      "name",
      "spec",
      "strictness"
    ],
    "mutating": true,
    "path": "/v1/generic-parts/{generic_part_id}",
    "pathParams": [
      "generic_part_id"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "detail",
    "verb": "PUT"
  },
  "update_part_fields": {
    "argOrder": [
      "part_key",
      "fields"
    ],
    "bodyParams": [
      "fields"
    ],
    "mutating": true,
    "path": "/v1/parts/{part_key}",
    "pathParams": [
      "part_key"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "detail",
    "verb": "PATCH"
  },
  "update_part_price": {
    "argOrder": [
      "part_key",
      "unit_price",
      "ext_price"
    ],
    "bodyParams": [
      "ext_price",
      "unit_price"
    ],
    "mutating": true,
    "path": "/v1/parts/{part_key}/price",
    "pathParams": [
      "part_key"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "detail",
    "verb": "PUT"
  },
  "update_purchase_order": {
    "argOrder": [
      "po_id",
      "vendor_id",
      "purchase_date",
      "notes"
    ],
    "bodyParams": [
      "notes",
      "purchase_date",
      "vendor_id"
    ],
    "mutating": true,
    "path": "/v1/purchase-orders/{po_id}",
    "pathParams": [
      "po_id"
    ],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "detail",
    "verb": "PATCH"
  },
  "update_vendor": {
    "argOrder": [
      "vendor_id",
      "name",
      "url",
      "favicon_path"
    ],
    "bodyParams": [
      "favicon_path",
      "name",
      "url",
      "vendor_id"
    ],
    "mutating": true,
    "path": "/v1/vendors",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": "detail",
    "verb": "PUT"
  },
  "validate_digikey_session": {
    "argOrder": [],
    "bodyParams": [],
    "mutating": false,
    "path": "/v1/distributors/digikey/session/validate",
    "pathParams": [],
    "queryParams": [],
    "rawBody": false,
    "unwrap": null,
    "verb": "POST"
  }
};
