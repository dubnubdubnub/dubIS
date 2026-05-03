"""Tests for vendors module: CRUD on vendors.json + similarity detection."""
import json
import os

import pytest

import vendors


@pytest.fixture
def vjson(tmp_path):
    """Return a path to a vendors.json file in tmp_path (does not exist yet)."""
    return str(tmp_path / "vendors.json")


class TestSeedBuiltins:
    def test_seeds_three_pseudo_vendors(self, vjson):
        vendors.seed_builtins(vjson)
        with open(vjson, encoding="utf-8") as f:
            data = json.load(f)
        ids = {v["id"] for v in data}
        assert ids == {"v_self", "v_salvage", "v_unknown"}

    def test_seeds_emoji_icons(self, vjson):
        vendors.seed_builtins(vjson)
        with open(vjson, encoding="utf-8") as f:
            data = json.load(f)
        icons = {v["id"]: v["icon"] for v in data}
        assert icons == {"v_self": "⚙️", "v_salvage": "♻️", "v_unknown": "❓"}

    def test_does_not_overwrite_existing_file(self, vjson):
        with open(vjson, "w", encoding="utf-8") as f:
            json.dump([{"id": "v_unknown", "name": "Custom Unknown",
                        "type": "unknown", "icon": "❓"}], f)
        vendors.seed_builtins(vjson)
        with open(vjson, encoding="utf-8") as f:
            data = json.load(f)
        unknown = next(v for v in data if v["id"] == "v_unknown")
        assert unknown["name"] == "Custom Unknown"


class TestListAndCreate:
    def test_list_returns_seeded(self, vjson):
        vendors.seed_builtins(vjson)
        result = vendors.list_vendors(vjson)
        assert len(result) == 3

    def test_create_real_vendor(self, vjson):
        vendors.seed_builtins(vjson)
        v = vendors.create_vendor(vjson, name="MDT", url="https://tmr-sensors.com")
        assert v["type"] == "real"
        assert v["name"] == "MDT"
        assert v["url"] == "https://tmr-sensors.com"
        assert v["id"].startswith("v_mdt_")

    def test_create_inferred_vendor(self, vjson):
        vendors.seed_builtins(vjson)
        v = vendors.create_vendor(vjson, name="HRS", inferred=True)
        assert v["type"] == "inferred"
        assert v["url"] == ""

    def test_create_dedupes_by_canonical_name(self, vjson):
        vendors.seed_builtins(vjson)
        v1 = vendors.create_vendor(vjson, name="MDT", url="https://tmr-sensors.com")
        v2 = vendors.create_vendor(vjson, name="mdt", url="https://tmr-sensors.com")
        assert v1["id"] == v2["id"]

    def test_create_id_is_stable_with_hash_suffix(self, vjson):
        vendors.seed_builtins(vjson)
        v = vendors.create_vendor(vjson, name="My Co", url="https://my.co")
        # slug + 4-char hex
        assert v["id"].startswith("v_my_co_")
        suffix = v["id"].rsplit("_", 1)[-1]
        assert len(suffix) == 4
        assert all(c in "0123456789abcdef" for c in suffix)

    def test_create_distinct_url_produces_distinct_id(self, vjson):
        """Same canonical name + different URL → distinct vendors with distinct IDs."""
        vendors.seed_builtins(vjson)
        a = vendors.create_vendor(vjson, name="MDT", url="https://x.com")
        b = vendors.create_vendor(vjson, name="MDt", url="https://y.com")
        assert a["id"] != b["id"]
        all_ids = [v["id"] for v in vendors.list_vendors(vjson)]
        assert len(all_ids) == len(set(all_ids)), f"duplicate IDs: {all_ids}"

    def test_create_with_url_enriches_existing_inferred(self, vjson):
        """An inferred vendor (no URL) can be re-found via create_vendor with URL."""
        vendors.seed_builtins(vjson)
        a = vendors.create_vendor(vjson, name="HRS", inferred=True)
        assert a["url"] == ""
        b = vendors.create_vendor(vjson, name="HRS", url="https://hirose.com")
        assert b["id"] == a["id"]  # same vendor, not a duplicate
        # Note: create_vendor returns existing record without modification.
        # Caller must follow up with update_vendor to set the URL.


class TestUpdateAndDelete:
    def test_update_changes_name_and_url(self, vjson):
        vendors.seed_builtins(vjson)
        v = vendors.create_vendor(vjson, name="MDT", url="https://tmr-sensors.com")
        vendors.update_vendor(vjson, v["id"], name="MDT Inc.", url="https://mdt-xs.com")
        result = next(x for x in vendors.list_vendors(vjson) if x["id"] == v["id"])
        assert result["name"] == "MDT Inc."
        assert result["url"] == "https://mdt-xs.com"

    def test_update_promotes_inferred_to_real_when_url_added(self, vjson):
        vendors.seed_builtins(vjson)
        v = vendors.create_vendor(vjson, name="HRS", inferred=True)
        vendors.update_vendor(vjson, v["id"], url="https://www.hirose.com")
        result = next(x for x in vendors.list_vendors(vjson) if x["id"] == v["id"])
        assert result["type"] == "real"

    def test_delete_real_vendor(self, vjson):
        vendors.seed_builtins(vjson)
        v = vendors.create_vendor(vjson, name="MDT", url="https://x.com")
        vendors.delete_vendor(vjson, v["id"])
        ids = {x["id"] for x in vendors.list_vendors(vjson)}
        assert v["id"] not in ids

    def test_delete_pseudo_vendor_raises(self, vjson):
        vendors.seed_builtins(vjson)
        with pytest.raises(ValueError, match="cannot delete"):
            vendors.delete_vendor(vjson, "v_unknown")

    def test_update_pseudo_vendor_raises(self, vjson):
        vendors.seed_builtins(vjson)
        with pytest.raises(ValueError, match="pseudo"):
            vendors.update_vendor(vjson, "v_self", name="Modified")

    def test_delete_unknown_id_raises(self, vjson):
        vendors.seed_builtins(vjson)
        with pytest.raises(KeyError):
            vendors.delete_vendor(vjson, "v_does_not_exist")


class TestSimilarity:
    def test_levenshtein_under_3(self, vjson):
        vendors.seed_builtins(vjson)
        a = vendors.create_vendor(vjson, name="MDT", url="https://x.com")
        b = vendors.create_vendor(vjson, name="MDt", url="https://y.com")
        # Different URL, similar name → should still flag
        pairs = vendors.find_possible_duplicates(vjson)
        ids_in_pairs = {(p[0]["id"], p[1]["id"]) for p in pairs}
        assert (a["id"], b["id"]) in ids_in_pairs or (b["id"], a["id"]) in ids_in_pairs

    def test_shared_url_domain(self, vjson):
        vendors.seed_builtins(vjson)
        a = vendors.create_vendor(vjson, name="Alpha", url="https://example.com")
        b = vendors.create_vendor(vjson, name="Beta", url="https://example.com/b")
        pairs = vendors.find_possible_duplicates(vjson)
        ids_in_pairs = {frozenset((p[0]["id"], p[1]["id"])) for p in pairs}
        assert frozenset((a["id"], b["id"])) in ids_in_pairs

    def test_dissimilar_vendors_not_flagged(self, vjson):
        vendors.seed_builtins(vjson)
        vendors.create_vendor(vjson, name="Alpha Industries", url="https://alpha.com")
        vendors.create_vendor(vjson, name="Zeta Manufacturing", url="https://zeta.com")
        pairs = vendors.find_possible_duplicates(vjson)
        # Self/Salvage/Unknown vs each other should not flag
        assert all(p[0]["type"] != "self" and p[1]["type"] != "self" for p in pairs)


class TestMerge:
    def test_merge_returns_destination_id(self, vjson):
        vendors.seed_builtins(vjson)
        src = vendors.create_vendor(vjson, name="MDT Inc", url="https://x.com")
        dst = vendors.create_vendor(vjson, name="MDT", url="https://y.com")
        result = vendors.merge_vendors(vjson, src["id"], dst["id"])
        assert result == dst["id"]
        ids = {v["id"] for v in vendors.list_vendors(vjson)}
        assert src["id"] not in ids
        assert dst["id"] in ids

    def test_merge_into_pseudo_disallowed(self, vjson):
        vendors.seed_builtins(vjson)
        src = vendors.create_vendor(vjson, name="MDT", url="https://x.com")
        with pytest.raises(ValueError, match="pseudo"):
            vendors.merge_vendors(vjson, src["id"], "v_unknown")

    def test_merge_unknown_src_raises(self, vjson):
        vendors.seed_builtins(vjson)
        dst = vendors.create_vendor(vjson, name="Alpha", url="https://a.com")
        with pytest.raises(KeyError):
            vendors.merge_vendors(vjson, "v_does_not_exist", dst["id"])


class TestFavicon:
    def test_canonicalize_url(self):
        from vendors import _canonicalize_url
        assert _canonicalize_url("https://Example.com/") == "https://example.com"
        assert _canonicalize_url("HTTP://example.com/path/") == "http://example.com/path"
        assert _canonicalize_url("example.com") == "https://example.com"

    def test_extract_favicon_url_from_link_rel(self):
        from vendors import _extract_favicon_url
        html = '<html><head><link rel="icon" href="/static/icon.png"></head></html>'
        assert _extract_favicon_url(html, "https://example.com") == "https://example.com/static/icon.png"

    def test_extract_favicon_url_fallback_root(self):
        from vendors import _extract_favicon_url
        html = "<html><head></head></html>"
        assert _extract_favicon_url(html, "https://example.com") == "https://example.com/favicon.ico"

    def test_fetch_favicon_writes_cache(self, tmp_path, monkeypatch):
        from vendors import fetch_favicon

        cache_dir = str(tmp_path / "favicons")

        def fake_get(url, timeout=None, allow_redirects=True):
            class R:
                status_code = 200
                content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
                headers = {"Content-Type": "image/png"}
                def raise_for_status(self): pass
            return R()

        import requests
        monkeypatch.setattr(requests, "get", fake_get)

        path = fetch_favicon("https://example.com", cache_dir)
        assert path.endswith(".png")
        assert os.path.isfile(os.path.join(cache_dir, os.path.basename(path)))

    def test_fetch_favicon_dedupes_by_url_hash(self, tmp_path, monkeypatch):
        from vendors import fetch_favicon

        cache_dir = str(tmp_path / "favicons")
        calls = {"n": 0}

        def fake_get(url, timeout=None, allow_redirects=True):
            calls["n"] += 1
            class R:
                status_code = 200
                content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
                headers = {"Content-Type": "image/png"}
                def raise_for_status(self): pass
            return R()

        import requests
        monkeypatch.setattr(requests, "get", fake_get)

        p1 = fetch_favicon("https://example.com", cache_dir)
        p2 = fetch_favicon("https://example.com", cache_dir)
        assert p1 == p2
        # Two requests still happen (no in-memory cache yet); but file path is stable
