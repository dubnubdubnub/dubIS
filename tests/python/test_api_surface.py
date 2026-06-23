"""Freeze the public pywebview API surface of ``InventoryApi``.

pywebview's ``webview/util.py:get_functions()`` registers exactly the attributes
that (a) do not start with ``_`` and (b) pass ``inspect.ismethod()`` — i.e. bound
instance methods. Those become ``window.pywebview.api.<name>`` and the JS frontend
calls them *positionally* via the ``api("name", ...args)`` bridge in ``js/api.js``.

This test freezes that surface — the method-name set plus each method's parameter
shape (names, order, defaults) — so the planned split of ``InventoryApi`` into
facades cannot silently rename, drop, reorder-params, or change-a-default on any
method the frontend depends on. It is the safety net the facade refactor is
verified against.

Annotations and return types are intentionally excluded from the frozen signature:
pywebview only passes positional args, so only parameter names/order/defaults are
part of the JS contract, and dropping annotations keeps this stable across Python
versions (string annotations render differently between interpreters).

If this fails after an *intentional* API change, update ``FROZEN_SURFACE``
deliberately — and check whether ``js/`` callers and the generated
``tests/fixtures/`` need updating too.
"""
import inspect

from inventory_api import InventoryApi

# Hardcoded in js/api.js whenPywebviewReady(): the bridge is probed for this exact
# method name to detect readiness. Losing/renaming it hangs app startup silently.
SENTINEL = "load_preferences"

# Public @staticmethods (NOT part of the pywebview bridge — staticmethods fail
# inspect.ismethod — but public API other Python code uses; assert they survive).
PUBLIC_STATICS = ("fix_double_utf8", "get_part_key")

# Public class attributes read directly by other modules/tests
# (mfg_direct_import.py, test_cache_db.py, test_real_data.py).
PUBLIC_CLASS_ATTRS = (
    "FIELDNAMES",
    "ADJ_FIELDNAMES",
    "SECTION_ORDER",
    "FLAT_SECTION_ORDER",
    "SECTION_HIERARCHY",
)

# name -> annotation-free parameter signature. The frozen pywebview surface.
FROZEN_SURFACE = {
    'add_generic_member': '(generic_part_id, part_id)',
    'adjust_part': "(adj_type, part_key, quantity, note='', source='')",
    'bench_mark': "(label, detail='')",
    'check_digikey_session': '()',
    'clear_mouser_api_key': '()',
    'confirm_close': '()',
    'consume_bom': "(matches_json, board_qty, bom_name, note='', source='')",
    'convert_xls_to_csv': '(path)',
    'create_generic_part': '(name, part_type, spec_json, strictness_json)',
    'create_purchase_order_with_items': (
        '(vendor_id, source_file_b64, source_file_name, purchase_date, notes, line_items_json)'
    ),
    'create_saved_search': '(generic_part_id, name, tag_state_json, search_text, frozen_members_json)',
    'delete_last_purchase_order': '()',
    'delete_purchase_order': '(po_id)',
    'delete_saved_search': '(search_id)',
    'delete_vendor': '(vendor_id)',
    'detect_columns': '(headers_json)',
    'exclude_generic_member': '(generic_part_id, part_id)',
    'extract_spec': '(part_key)',
    'extract_spec_from_value': '(part_type, value_str, package_str)',
    'fetch_digikey_product': '(part_number)',
    'fetch_favicon': '(url)',
    'fetch_lcsc_product': '(product_code)',
    'fetch_mouser_product': '(part_number)',
    'fetch_pololu_product': '(sku)',
    'get_digikey_login_status': '()',
    'get_last_po_quantity': '(part_key)',
    'get_mouser_api_key_status': '()',
    'get_po_source_preview': '(po_id)',
    'get_po_with_items': '(po_id)',
    'get_poll_api_info': '()',
    'get_price_summary': '(part_key)',
    'get_warnings': '()',
    'import_purchases': '(rows_json)',
    'install_tesseract': '()',
    'list_generic_parts': '()',
    'list_purchase_orders': '()',
    'list_saved_searches': '(generic_part_id)',
    'list_vendors': '()',
    'load_file': '(path)',
    'load_preferences': '()',
    'logout_digikey': '()',
    'match_part': "(mpn, manufacturer='')",
    'merge_vendors': '(src_id, dst_id)',
    'ocr_engine_available': '()',
    'ocr_overlay_b64': "(file_b64, file_name, template='generic')",
    'open_file_dialog': "(title='Select CSV file', default_dir=None)",
    'open_source_file': '(po_id)',
    'parse_source_file': "(path, template='generic')",
    'parse_source_file_b64': "(file_b64, file_name, template='generic')",
    'rebuild_inventory': '()',
    'record_fetched_prices': '(part_key, distributor, price_tiers)',
    'remove_generic_member': '(generic_part_id, part_id)',
    'remove_last_adjustments': '(count)',
    'remove_last_purchases': '(count)',
    'resolve_bom_spec': '(part_type, value, package)',
    'rollback_source': '(source)',
    'save_file_dialog': "(content, default_name='export.csv', default_dir=None, links_json=None)",
    'save_preferences': '(prefs_json)',
    'set_bom_dirty': '(dirty)',
    'set_mouser_api_key': '(key)',
    'set_poll_api_port': '(port)',
    'set_preferred_member': '(generic_part_id, part_id)',
    'shutdown': '()',
    'start_digikey_login': '()',
    'start_scan_session': "(template='generic')",
    'sync_digikey_cookies': '()',
    'update_generic_part': '(generic_part_id, name, spec_json, strictness_json)',
    'update_part_fields': '(part_key, fields_json)',
    'update_part_price': '(part_key, unit_price=None, ext_price=None)',
    'update_purchase_order': "(po_id, vendor_id='', purchase_date='', notes='')",
    'update_vendor': "(vendor_id='', name='', url='', favicon_path='')",
    'validate_digikey_session': '()',
}


def _norm_sig(method) -> str:
    """Annotation-free signature: param names, order, defaults — what the JS bridge depends on."""
    parts = []
    for p in inspect.signature(method).parameters.values():
        if p.kind is p.VAR_POSITIONAL:
            parts.append("*" + p.name)
        elif p.kind is p.VAR_KEYWORD:
            parts.append("**" + p.name)
        elif p.default is p.empty:
            parts.append(p.name)
        else:
            parts.append(f"{p.name}={p.default!r}")
    return "(" + ", ".join(parts) + ")"


def _live_surface() -> dict[str, str]:
    """The exact filter pywebview applies: public + bound-method, mapped to its signature."""
    api = InventoryApi()
    return {
        n: _norm_sig(getattr(api, n))
        for n in dir(api)
        if not n.startswith("_") and inspect.ismethod(getattr(api, n))
    }


def test_public_method_names_frozen():
    live = set(_live_surface())
    frozen = set(FROZEN_SURFACE)
    assert live == frozen, (
        "pywebview public method surface changed — this breaks/loses JS bridge methods.\n"
        f"  ADDED (not in freeze):   {sorted(live - frozen)}\n"
        f"  REMOVED (gone from api): {sorted(frozen - live)}\n"
        "If intentional, update FROZEN_SURFACE and check js/ callers + tests/fixtures/."
    )


def test_public_method_signatures_frozen():
    live = _live_surface()
    drift = {
        n: (FROZEN_SURFACE[n], live[n])
        for n in FROZEN_SURFACE
        if n in live and live[n] != FROZEN_SURFACE[n]
    }
    assert not drift, (
        "parameter signature(s) changed — pywebview passes positional args, so this "
        "silently corrupts JS call sites:\n"
        + "\n".join(
            f"  {n}: frozen {frozen_sig}  !=  live {live_sig}"
            for n, (frozen_sig, live_sig) in sorted(drift.items())
        )
    )


def test_pywebview_ready_sentinel_present():
    assert SENTINEL in _live_surface(), (
        f"{SENTINEL!r} is hardcoded in js/api.js whenPywebviewReady(); removing/renaming it "
        "hangs startup silently."
    )


def test_public_statics_and_class_attrs_present():
    api = InventoryApi()
    for name in PUBLIC_STATICS:
        assert callable(getattr(api, name, None)), f"missing public static {name!r}"
    for name in PUBLIC_CLASS_ATTRS:
        assert getattr(type(api), name, None) is not None, f"missing public class attr {name!r}"
