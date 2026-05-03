"""Vendor catalog: CRUD on data/vendors.json + similarity detection."""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any
from urllib.parse import urlparse

PSEUDO_IDS = {"v_self", "v_salvage", "v_unknown"}

BUILTINS = [
    {"id": "v_self",    "name": "Self",    "type": "self",    "icon": "⚙️",
     "url": "", "favicon_path": ""},
    {"id": "v_salvage", "name": "Salvage", "type": "salvage", "icon": "♻️",
     "url": "", "favicon_path": ""},
    {"id": "v_unknown", "name": "Unknown", "type": "unknown", "icon": "❓",
     "url": "", "favicon_path": ""},
]


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return s or "vendor"


def _make_id(name: str, url: str = "") -> str:
    slug = _slugify(name)
    h = hashlib.md5((name.lower() + "|" + url.strip().lower()).encode("utf-8")).hexdigest()[:4]
    return f"v_{slug}_{h}"


def _read(path: str) -> list[dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write(path: str, data: list[dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def seed_builtins(path: str) -> None:
    """Create vendors.json with built-in pseudo-vendors if it doesn't exist."""
    if os.path.exists(path):
        return
    _write(path, [dict(b) for b in BUILTINS])


def list_vendors(path: str) -> list[dict[str, Any]]:
    """Return all vendors."""
    return _read(path)


def find_by_id(path: str, vendor_id: str) -> dict[str, Any] | None:
    return next((v for v in _read(path) if v["id"] == vendor_id), None)


def find_by_canonical_name(path: str, name: str,
                            url: str = "") -> dict[str, Any] | None:
    """Find a vendor whose canonical slug matches name and (if url given) whose url matches."""
    target_slug = _slugify(name)
    target_url = url.strip().lower()
    for v in _read(path):
        if _slugify(v["name"]) != target_slug:
            continue
        if target_url and v.get("url", "").strip().lower() != target_url:
            continue
        return v
    return None


def create_vendor(path: str, name: str, url: str = "",
                   inferred: bool = False) -> dict[str, Any]:
    """Create a vendor (or return existing if name+url canonicalize to one we have)."""
    name = name.strip()
    if not name:
        raise ValueError("vendor name must not be empty")
    existing = find_by_canonical_name(path, name, url=url)
    if existing:
        return existing
    vtype = "inferred" if inferred else ("real" if url else "inferred")
    new_v = {
        "id": _make_id(name, url),
        "name": name,
        "url": url.strip(),
        "favicon_path": "",
        "type": vtype,
        "icon": "",
    }
    data = _read(path)
    data.append(new_v)
    _write(path, data)
    return new_v


def update_vendor(path: str, vendor_id: str,
                   name: str | None = None,
                   url: str | None = None,
                   favicon_path: str | None = None) -> dict[str, Any]:
    """Update fields on a vendor. Promotes inferred → real if URL added."""
    data = _read(path)
    for v in data:
        if v["id"] == vendor_id:
            if name is not None:
                v["name"] = name.strip()
            if url is not None:
                v["url"] = url.strip()
                if v["type"] == "inferred" and v["url"]:
                    v["type"] = "real"
            if favicon_path is not None:
                v["favicon_path"] = favicon_path
            _write(path, data)
            return v
    raise KeyError(vendor_id)


def delete_vendor(path: str, vendor_id: str) -> None:
    if vendor_id in PSEUDO_IDS:
        raise ValueError(f"cannot delete pseudo-vendor {vendor_id}")
    data = [v for v in _read(path) if v["id"] != vendor_id]
    _write(path, data)


def merge_vendors(path: str, src_id: str, dst_id: str) -> str:
    """Remove src vendor, return dst id. Caller must reassign POs separately."""
    if dst_id in PSEUDO_IDS:
        raise ValueError(f"cannot merge into pseudo-vendor {dst_id}")
    if src_id in PSEUDO_IDS:
        raise ValueError(f"cannot merge from pseudo-vendor {src_id}")
    data = _read(path)
    if not any(v["id"] == dst_id for v in data):
        raise KeyError(dst_id)
    data = [v for v in data if v["id"] != src_id]
    _write(path, data)
    return dst_id


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[-1]


def find_possible_duplicates(path: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Find vendor pairs that look like duplicates.

    Criteria: name Levenshtein ≤3 (ignoring case) OR shared URL domain.
    Pseudo-vendors are excluded.
    """
    real = [v for v in _read(path) if v["id"] not in PSEUDO_IDS]
    pairs: list[tuple[dict, dict]] = []
    for i in range(len(real)):
        for j in range(i + 1, len(real)):
            a, b = real[i], real[j]
            name_close = _levenshtein(a["name"].lower(), b["name"].lower()) <= 3
            domain_a = urlparse(a.get("url", "")).netloc.lower()
            domain_b = urlparse(b.get("url", "")).netloc.lower()
            domain_match = bool(domain_a) and domain_a == domain_b
            if name_close or domain_match:
                pairs.append((a, b))
    return pairs
