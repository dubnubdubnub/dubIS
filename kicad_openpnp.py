"""KiCad + OpenPnP integration — S-expr parsing, footprint fetching, XML generation."""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── S-expression parser ─────────────────────────────────────────────────────

def parse_sexpr(text: str) -> list:
    """Minimal recursive-descent S-expression parser for KiCad files.

    Converts ``(foo "bar" 1.5 (baz 2))`` into ``['foo', 'bar', '1.5', ['baz', '2']]``.
    Strings stay as Python str; no numeric coercion (caller decides).
    """
    pos = 0
    length = len(text)

    def _skip_ws():
        nonlocal pos
        while pos < length and text[pos] in " \t\n\r":
            pos += 1

    def _read_string() -> str:
        nonlocal pos
        pos += 1  # skip opening quote
        chars: list[str] = []
        while pos < length:
            ch = text[pos]
            if ch == "\\":
                pos += 1
                if pos < length:
                    chars.append(text[pos])
            elif ch == '"':
                pos += 1
                return "".join(chars)
            else:
                chars.append(ch)
            pos += 1
        return "".join(chars)

    def _read_atom() -> str:
        nonlocal pos
        start = pos
        while pos < length and text[pos] not in " \t\n\r()\"":
            pos += 1
        return text[start:pos]

    def _read_list() -> list:
        nonlocal pos
        pos += 1  # skip '('
        items: list = []
        while True:
            _skip_ws()
            if pos >= length:
                break
            if text[pos] == ")":
                pos += 1
                break
            items.append(_read_expr())
        return items

    def _read_expr():
        _skip_ws()
        if pos >= length:
            return ""
        if text[pos] == "(":
            return _read_list()
        if text[pos] == '"':
            return _read_string()
        return _read_atom()

    _skip_ws()
    if pos < length and text[pos] == "(":
        return _read_list()
    return [_read_expr()]


def _sexpr_find(node: list, tag: str) -> list | None:
    """Find first child list whose first element == tag."""
    for item in node:
        if isinstance(item, list) and item and item[0] == tag:
            return item
    return None


def _sexpr_find_all(node: list, tag: str):
    """Yield all child lists whose first element == tag."""
    for item in node:
        if isinstance(item, list) and item and item[0] == tag:
            yield item


def _sexpr_value(node: list, tag: str, default: str = "") -> str:
    """Get the string value of (tag value) inside node."""
    child = _sexpr_find(node, tag)
    if child and len(child) >= 2:
        return str(child[1])
    return default


# ── KiCad project scanner ───────────────────────────────────────────────────

# Field names to match (case-insensitive) for LCSC / MPN extraction
_LCSC_FIELDS = {"lcsc", "lcsc part", "lcsc part number", "jlcpcb", "jlcpcb part", "jlcpcb part #"}
_MPN_FIELDS = {"mpn", "manufacturer part number", "mfr. part #", "mfr part", "manufacturer_part_number"}


def scan_kicad_project(project_path: str) -> dict:
    """Scan a KiCad project directory and return BOM-like part list.

    Returns::

        {
            "name": "BoardName",
            "kicad_pro": "BoardName.kicad_pro",
            "parts": [{ref, value, footprint, lcsc, mpn, dnp}, ...],
            "last_scan": "ISO timestamp"
        }
    """
    project_path = os.path.normpath(project_path)
    if not os.path.isdir(project_path):
        raise ValueError(f"Not a directory: {project_path}")

    # Find .kicad_pro file
    pro_files = [f for f in os.listdir(project_path) if f.endswith(".kicad_pro")]
    if not pro_files:
        raise ValueError(f"No .kicad_pro file found in {project_path}")
    kicad_pro = pro_files[0]
    board_name = os.path.splitext(kicad_pro)[0]

    # Find all .kicad_sch files recursively
    sch_files: list[str] = []
    for root, _dirs, files in os.walk(project_path):
        for f in files:
            if f.endswith(".kicad_sch"):
                sch_files.append(os.path.join(root, f))

    if not sch_files:
        raise ValueError(f"No .kicad_sch files found in {project_path}")

    parts: list[dict] = []
    for sch_path in sch_files:
        parts.extend(_parse_schematic(sch_path))

    return {
        "name": board_name,
        "kicad_pro": kicad_pro,
        "parts": parts,
        "last_scan": datetime.now(timezone.utc).isoformat(),
    }


def _parse_schematic(sch_path: str) -> list[dict]:
    """Parse a single .kicad_sch file for symbol instances."""
    with open(sch_path, encoding="utf-8") as f:
        text = f.read()

    tree = parse_sexpr(text)
    parts: list[dict] = []

    for sym in _sexpr_find_all(tree, "symbol"):
        # Skip power symbols and lib_id starting with "power:"
        lib_id = _sexpr_value(sym, "lib_id")
        if lib_id.startswith("power:"):
            continue

        # Check in_bom flag
        in_bom = _sexpr_value(sym, "in_bom")
        if in_bom == "no":
            continue

        ref = ""
        value = ""
        footprint = ""
        lcsc = ""
        mpn = ""
        dnp = False

        # Check DNP flag
        dnp_node = _sexpr_find(sym, "dnp")
        if dnp_node is not None:
            dnp_val = dnp_node[1] if len(dnp_node) >= 2 else "yes"
            dnp = str(dnp_val).lower() != "no"

        # Extract properties
        for prop in _sexpr_find_all(sym, "property"):
            if len(prop) < 3:
                continue
            prop_name = str(prop[1])
            prop_value = str(prop[2])
            lower_name = prop_name.lower().strip()

            if lower_name == "reference":
                ref = prop_value
            elif lower_name == "value":
                value = prop_value
            elif lower_name == "footprint":
                footprint = prop_value
            elif lower_name in _LCSC_FIELDS:
                if prop_value and prop_value != "~":
                    lcsc = prop_value.strip()
            elif lower_name in _MPN_FIELDS:
                if prop_value and prop_value != "~":
                    mpn = prop_value.strip()

        # Skip virtual refs like #PWR, #FLG
        if ref.startswith("#") or not ref:
            continue

        parts.append({
            "ref": ref,
            "value": value,
            "footprint": _simplify_footprint(footprint),
            "footprint_full": footprint,
            "lcsc": lcsc,
            "mpn": mpn,
            "dnp": dnp,
        })

    return parts


def _simplify_footprint(fp: str) -> str:
    """Strip KiCad library prefix and metric suffix.

    ``Resistor_SMD:R_0402_1005Metric`` → ``R_0402``
    """
    # Remove library prefix
    if ":" in fp:
        fp = fp.split(":", 1)[1]
    # Remove _xxyyMetric suffix
    fp = re.sub(r"_\d{4}Metric$", "", fp)
    return fp


# ── EasyEDA footprint fetcher ───────────────────────────────────────────────

EASYEDA_API = "https://easyeda.com/api"
_UNITS_TO_MM = 0.254  # 1 EasyEDA unit = 10 mils = 0.254 mm


def fetch_easyeda_footprint(lcsc_id: str) -> dict | None:
    """Fetch footprint data from EasyEDA for an LCSC part number.

    Three-step API flow:
    1. products/{id}/components → component UUID
    2. components/{uuid} → package UUID
    3. components/{pkg_uuid} → footprint data

    Returns raw footprint result dict, or None on failure.
    """
    lcsc_id = lcsc_id.strip().upper()
    if not re.match(r"^C\d{4,}$", lcsc_id):
        raise ValueError(f"Invalid LCSC part number: {lcsc_id}")

    try:
        # Step 1: Get component UUID
        data1 = _easyeda_get(f"{EASYEDA_API}/products/{lcsc_id}/components")
        if not data1 or not data1.get("success"):
            return None
        result1 = data1.get("result")
        if not result1:
            return None
        comp_uuid = result1.get("uuid")
        if not comp_uuid:
            return None

        # Step 2: Get package UUID
        data2 = _easyeda_get(f"{EASYEDA_API}/components/{comp_uuid}")
        if not data2:
            return None
        result2 = data2.get("result", {})
        pkg_detail = result2.get("packageDetail")
        if not pkg_detail:
            return None
        pkg_uuid = pkg_detail.get("uuid")
        if not pkg_uuid:
            return None

        # Step 3: Get footprint data
        data3 = _easyeda_get(f"{EASYEDA_API}/components/{pkg_uuid}")
        if not data3:
            return None
        return data3.get("result")

    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        logger.warning("EasyEDA fetch failed for %s: %s", lcsc_id, exc)
        return None


def _easyeda_get(url: str) -> dict | None:
    """GET JSON from EasyEDA API."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_easyeda_footprint(data: dict, package_id: str) -> dict:
    """Parse EasyEDA footprint data into our plain-dict format.

    Returns::

        {
            "body_width": float, "body_height": float,
            "pads": [{"name", "x", "y", "width", "height", "rotation", "roundness"}, ...]
        }
    """
    pads_raw = _extract_easyeda_pads(data)
    if not pads_raw:
        raise ValueError(f"No pad data found for {package_id}")

    # Convert to mm
    pads_mm: list[dict] = []
    for p in pads_raw:
        x_mm = p["x"] * _UNITS_TO_MM
        y_mm = -p["y"] * _UNITS_TO_MM  # invert Y axis
        w_mm = p["width"] * _UNITS_TO_MM
        h_mm = p["height"] * _UNITS_TO_MM
        shape = p.get("shape", "RECT").upper()
        if shape in ("ROUND", "CIRCLE"):
            roundness = 100.0
        elif shape == "OVAL":
            roundness = 50.0
        else:
            roundness = 0.0
        pads_mm.append({
            "name": p["name"],
            "x": round(x_mm, 4),
            "y": round(y_mm, 4),
            "width": round(w_mm, 4),
            "height": round(h_mm, 4),
            "rotation": p.get("rotation", 0.0),
            "roundness": roundness,
        })

    # Center footprint
    if pads_mm:
        cx = sum(p["x"] for p in pads_mm) / len(pads_mm)
        cy = sum(p["y"] for p in pads_mm) / len(pads_mm)
        for p in pads_mm:
            p["x"] = round(p["x"] - cx, 4)
            p["y"] = round(p["y"] - cy, 4)

    # Calculate body size
    body_w, body_h = _calc_body_size(pads_mm)

    return {
        "body_width": round(body_w, 4),
        "body_height": round(body_h, 4),
        "pads": pads_mm,
    }


def _extract_easyeda_pads(data: dict) -> list[dict]:
    """Extract pad data from various EasyEDA response formats."""
    # Format 1: dataStr.shape array with PAD~ strings
    if "dataStr" in data:
        datastr = data["dataStr"]
        if isinstance(datastr, dict) and "shape" in datastr:
            shapes = datastr["shape"]
            if isinstance(shapes, list):
                pads = []
                for s in shapes:
                    if isinstance(s, str) and s.startswith("PAD~"):
                        pad = _parse_pad_string(s)
                        if pad:
                            pads.append(pad)
                if pads:
                    return pads

    # Format 2: Direct PAD key
    if "PAD" in data:
        return _pads_from_dict(data["PAD"])

    # Format 3: Nested JSON in dataStr
    if "dataStr" in data and isinstance(data["dataStr"], str):
        try:
            inner = json.loads(data["dataStr"])
            if "PAD" in inner:
                return _pads_from_dict(inner["PAD"])
        except (json.JSONDecodeError, TypeError):
            pass

    # Format 4: footprint.PAD
    if "footprint" in data and isinstance(data["footprint"], dict):
        if "PAD" in data["footprint"]:
            return _pads_from_dict(data["footprint"]["PAD"])

    return []


def _parse_pad_string(pad_str: str) -> dict | None:
    """Parse EasyEDA PAD string: PAD~SHAPE~x~y~w~h~layer~~number~0~coords~rotation~..."""
    parts = pad_str.split("~")
    if len(parts) < 9:
        return None
    try:
        shape = parts[1]
        x = float(parts[2])
        y = float(parts[3])
        width = float(parts[4])
        height = float(parts[5])
        name = parts[8] if parts[8] else "1"
        rotation = float(parts[11]) if len(parts) > 11 and parts[11] else 0.0
        if width <= 0 or height <= 0:
            return None
        return {
            "name": str(name), "shape": shape, "x": x, "y": y,
            "width": width, "height": height, "rotation": rotation,
        }
    except (ValueError, IndexError):
        return None


def _pads_from_dict(pad_dict: dict) -> list[dict]:
    """Convert EasyEDA PAD dict format to our internal list."""
    pads = []
    for key, val in pad_dict.items():
        if not isinstance(val, dict):
            continue
        try:
            name = val.get("number", val.get("name", key))
            w = float(val.get("width", 0))
            h = float(val.get("height", w))
            if w <= 0 or h <= 0:
                continue
            shape = val.get("shape", "RECT")
            if isinstance(shape, int):
                shape = {1: "RECT", 2: "ROUND", 3: "OVAL", 4: "ELLIPSE"}.get(shape, "RECT")
            pads.append({
                "name": str(name), "shape": str(shape).upper(),
                "x": float(val.get("x", 0)), "y": float(val.get("y", 0)),
                "width": w, "height": h,
                "rotation": float(val.get("rotation", 0)),
            })
        except (ValueError, TypeError):
            continue
    return pads


def _calc_body_size(pads: list[dict]) -> tuple[float, float]:
    """Calculate body dimensions from pad extents."""
    if not pads:
        return (1.0, 1.0)
    if len(pads) == 2:
        w = abs(pads[0]["x"] - pads[1]["x"])
        h = max(p["height"] for p in pads)
        return (max(w, 0.1), max(h, 0.1))
    min_x = min(p["x"] - p["width"] / 2 for p in pads)
    max_x = max(p["x"] + p["width"] / 2 for p in pads)
    min_y = min(p["y"] - p["height"] / 2 for p in pads)
    max_y = max(p["y"] + p["height"] / 2 for p in pads)
    return (max(max_x - min_x, 0.1), max(max_y - min_y, 0.1))


# ── KiCad .kicad_mod footprint parser ───────────────────────────────────────

_KICAD_DEFAULT_PATHS_WIN = [
    r"C:\Program Files\KiCad\8.0\share\kicad\footprints",
    r"C:\Program Files\KiCad\7.0\share\kicad\footprints",
    r"C:\Program Files\KiCad\share\kicad\footprints",
]


def find_kicad_lib_paths() -> list[str]:
    """Auto-detect KiCad footprint library paths on the system."""
    found: list[str] = []
    for p in _KICAD_DEFAULT_PATHS_WIN:
        if os.path.isdir(p):
            found.append(p)
    # Also check KICAD8_FOOTPRINT_DIR / KICAD7_FOOTPRINT_DIR env vars
    for env_var in ("KICAD8_FOOTPRINT_DIR", "KICAD7_FOOTPRINT_DIR", "KICAD_FOOTPRINT_DIR"):
        val = os.environ.get(env_var)
        if val and os.path.isdir(val) and val not in found:
            found.append(val)
    return found


def parse_kicad_footprint(fp_ref: str, lib_paths: list[str] | None = None) -> dict | None:
    """Parse a KiCad .kicad_mod file referenced by ``Library:Footprint``.

    Returns footprint dict in same format as ``parse_easyeda_footprint``, or None.
    """
    if ":" not in fp_ref:
        return None
    lib_name, fp_name = fp_ref.split(":", 1)
    if lib_paths is None:
        lib_paths = find_kicad_lib_paths()

    # Resolve .kicad_mod file
    mod_path = None
    for base in lib_paths:
        candidate = os.path.join(base, f"{lib_name}.pretty", f"{fp_name}.kicad_mod")
        if os.path.isfile(candidate):
            mod_path = candidate
            break

    if not mod_path:
        return None

    with open(mod_path, encoding="utf-8") as f:
        text = f.read()

    tree = parse_sexpr(text)
    return _parse_kicad_mod(tree)


def _parse_kicad_mod(tree: list) -> dict | None:
    """Extract pad data from a parsed .kicad_mod S-expression tree."""
    pads_mm: list[dict] = []

    for pad_node in _sexpr_find_all(tree, "pad"):
        # (pad "1" smd rect (at x y [rot]) (size w h) ...)
        if len(pad_node) < 4:
            continue
        name = str(pad_node[1])
        # pad_node[2] is pad type: smd, thru_hole, connect, np_thru_hole
        shape_str = str(pad_node[3])  # rect, roundrect, circle, oval, custom

        # Extract position
        at_node = _sexpr_find(pad_node, "at")
        if not at_node or len(at_node) < 3:
            continue
        x = float(at_node[1])
        y = -float(at_node[2])  # negate Y for OpenPnP convention
        rotation = float(at_node[3]) if len(at_node) >= 4 else 0.0

        # Extract size
        size_node = _sexpr_find(pad_node, "size")
        if not size_node or len(size_node) < 3:
            continue
        w = float(size_node[1])
        h = float(size_node[2])

        if w <= 0 or h <= 0:
            continue

        # Determine roundness
        if shape_str == "circle":
            roundness = 100.0
        elif shape_str == "oval":
            roundness = 50.0
        elif shape_str == "roundrect":
            rratio_node = _sexpr_find(pad_node, "roundrect_rratio")
            rratio = float(rratio_node[1]) if rratio_node and len(rratio_node) >= 2 else 0.25
            roundness = round(rratio * 200, 1)  # 0.25 ratio → 50% roundness
            roundness = min(roundness, 100.0)
        else:  # rect, custom
            roundness = 0.0

        pads_mm.append({
            "name": name,
            "x": round(x, 4),
            "y": round(y, 4),
            "width": round(w, 4),
            "height": round(h, 4),
            "rotation": rotation,
            "roundness": roundness,
        })

    if not pads_mm:
        return None

    # Center footprint
    cx = sum(p["x"] for p in pads_mm) / len(pads_mm)
    cy = sum(p["y"] for p in pads_mm) / len(pads_mm)
    for p in pads_mm:
        p["x"] = round(p["x"] - cx, 4)
        p["y"] = round(p["y"] - cy, 4)

    body_w, body_h = _calc_body_size(pads_mm)

    # Try courtyard or fab layer for body size
    body_from_layer = _body_from_layer(pads_mm[0], [])  # placeholder
    if body_from_layer:
        body_w, body_h = body_from_layer

    return {
        "body_width": round(body_w, 4),
        "body_height": round(body_h, 4),
        "pads": pads_mm,
    }


def _body_from_layer(_pad: dict, _tree: list) -> tuple[float, float] | None:
    """Extract body size from courtyard/fab layer lines. Placeholder for future."""
    return None


# ── OpenPnP XML generation ──────────────────────────────────────────────────

_DUBIS_MARKER = "dubIS-managed"


def generate_openpnp_packages_xml(openpnp_data: dict, config_path: str) -> str:
    """Generate or update packages.xml in the OpenPnP config directory.

    Preserves non-dubIS entries, adds/updates dubIS-managed entries.
    Returns the path to the written file.
    """
    packages_path = os.path.join(config_path, "packages.xml")

    # Load existing or create new
    if os.path.isfile(packages_path):
        tree = ET.parse(packages_path)
        root = tree.getroot()
    else:
        root = ET.Element("openpnp-packages")
        tree = ET.ElementTree(root)

    # Remove existing dubIS-managed packages
    for pkg_el in list(root):
        desc = pkg_el.get("description", "")
        if _DUBIS_MARKER in desc:
            root.remove(pkg_el)

    # Add dubIS-managed packages
    for part_key, meta in openpnp_data.get("parts", {}).items():
        fp = meta.get("footprint")
        if not fp or not fp.get("pads"):
            continue

        pkg_id = meta.get("package_id", part_key)
        pkg_el = ET.SubElement(root, "package",
                               id=pkg_id, description=f"{_DUBIS_MARKER}: {part_key}")

        fp_el = ET.SubElement(pkg_el, "footprint",
                              units="Millimeters",
                              **{"body-width": str(fp["body_width"]),
                                 "body-height": str(fp["body_height"])})

        for pad in fp["pads"]:
            ET.SubElement(fp_el, "pad",
                          name=str(pad["name"]),
                          x=str(pad["x"]), y=str(pad["y"]),
                          width=str(pad["width"]), height=str(pad["height"]),
                          rotation=str(pad.get("rotation", 0)),
                          roundness=str(pad.get("roundness", 0)))

    ET.indent(tree, space="  ")
    tree.write(packages_path, encoding="unicode", xml_declaration=True)
    return packages_path


def generate_openpnp_parts_xml(openpnp_data: dict, config_path: str) -> str:
    """Generate or update parts.xml in the OpenPnP config directory.

    Preserves non-dubIS entries, adds/updates dubIS-managed entries.
    Returns the path to the written file.
    """
    parts_path = os.path.join(config_path, "parts.xml")

    if os.path.isfile(parts_path):
        tree = ET.parse(parts_path)
        root = tree.getroot()
    else:
        root = ET.Element("openpnp-parts")
        tree = ET.ElementTree(root)

    # Remove existing dubIS-managed parts
    for part_el in list(root):
        name = part_el.get("name", "")
        if name.startswith(f"{_DUBIS_MARKER}:") or part_el.get("description", "").startswith(_DUBIS_MARKER):
            root.remove(part_el)

    # Add dubIS-managed parts
    for part_key, meta in openpnp_data.get("parts", {}).items():
        openpnp_id = meta.get("openpnp_id", part_key)
        pkg_id = meta.get("package_id", "")
        height = meta.get("height", 0)
        speed = meta.get("speed", 1.0)

        part_el = ET.SubElement(root, "part",
                                id=openpnp_id,
                                name=f"{_DUBIS_MARKER}: {part_key}",
                                description=f"{_DUBIS_MARKER}",
                                **{"package-id": pkg_id,
                                   "height": str(height),
                                   "speed": str(speed)})

    ET.indent(tree, space="  ")
    tree.write(parts_path, encoding="unicode", xml_declaration=True)
    return parts_path


# ── JSON persistence helpers ────────────────────────────────────────────────

def _data_path(filename: str) -> str:
    """Return path to a file in the data/ directory."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", filename)


def load_json(path: str) -> dict:
    """Load JSON file, return empty dict if missing/corrupt."""
    try:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load %s: %s", path, exc)
    return {}


def save_json(path: str, data: dict) -> None:
    """Write JSON file with pretty formatting."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_part_links() -> dict:
    """Load part_links.json."""
    data = load_json(_data_path("part_links.json"))
    if "links" not in data:
        data = {"links": {}, "version": 1}
    return data


def save_part_links(data: dict) -> None:
    """Save part_links.json."""
    save_json(_data_path("part_links.json"), data)


def load_openpnp_parts() -> dict:
    """Load openpnp_parts.json."""
    data = load_json(_data_path("openpnp_parts.json"))
    if "parts" not in data:
        data = {"parts": {}, "nozzle_tips": [], "package_nozzle_defaults": {},
                "openpnp_config_path": "", "version": 1}
    return data


def save_openpnp_parts(data: dict) -> None:
    """Save openpnp_parts.json."""
    save_json(_data_path("openpnp_parts.json"), data)


def load_kicad_projects() -> dict:
    """Load kicad_projects.json."""
    data = load_json(_data_path("kicad_projects.json"))
    if "projects" not in data:
        data = {"projects": {}}
    return data


def save_kicad_projects(data: dict) -> None:
    """Save kicad_projects.json."""
    save_json(_data_path("kicad_projects.json"), data)


def regenerate_pnp_part_map(openpnp_data: dict) -> None:
    """Rebuild pnp_part_map.json from openpnp_parts.json data.

    Maps {openpnp_id: part_key} so pnp_server.py works unchanged.
    """
    part_map: dict[str, str] = {}
    for part_key, meta in openpnp_data.get("parts", {}).items():
        openpnp_id = meta.get("openpnp_id")
        if openpnp_id:
            part_map[openpnp_id] = part_key
    save_json(_data_path("pnp_part_map.json"), part_map)
