"""Tests for /v1/feeders — the loading-station feeder entity API + the
AprilTag 36h11 print-at-100% sheet generator."""

from __future__ import annotations

import io

import cv2
import fitz
import numpy as np

from domain import part_registry
from server.routes.feeders import MM_TO_PT
from tests.python.helpers import make_api, make_part, write_ledger


def test_register_get_list(client):
    r = client.post("/v1/feeders/1/register", json={"feeder_type": "strip-feeder"})
    assert r.status_code == 200
    body = r.json()
    assert body["tag_id"] == "1"
    assert body["family"] == "apriltag_36h11"
    assert body["feeder_type"] == "strip-feeder"
    assert body["loaded"] is None

    r = client.get("/v1/feeders/1")
    assert r.status_code == 200
    assert r.json() == body

    r = client.get("/v1/feeders")
    assert r.status_code == 200
    assert r.json()["feeders"] == [body]


def test_get_unknown_feeder_is_404(client):
    r = client.get("/v1/feeders/nope")
    assert r.status_code == 404
    body = r.json()
    assert set(body.keys()) == {"error", "code", "detail"}
    assert body["code"] == "not_found"


def test_register_twice_is_4xx(client):
    r = client.post("/v1/feeders/1/register", json={"feeder_type": "strip-feeder"})
    assert r.status_code == 200
    r = client.post("/v1/feeders/1/register", json={"feeder_type": "strip-feeder"})
    assert r.status_code == 400
    assert r.json()["code"] == "value_error"


def test_load_before_register_is_4xx(client):
    r = client.post("/v1/feeders/99/load", json={"part_key": "C100000", "qty": 10})
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


def test_load_and_unload_roundtrip(client):
    client.post("/v1/feeders/1/register", json={"feeder_type": "strip-feeder"})
    r = client.post("/v1/feeders/1/load", json={"part_key": "C100000", "qty": 250})
    assert r.status_code == 200
    body = r.json()
    assert body["loaded"]["part_key"] == "C100000"
    assert body["loaded"]["qty"] == 250
    assert body["loaded"]["loaded_at"]

    # tape width auto-derived: seeded part is a 10kΩ 0402 resistor.
    assert body["loaded"]["tape_width_mm"] == 8.0

    r = client.post("/v1/feeders/1/unload")
    assert r.status_code == 200
    assert r.json()["loaded"] is None

    # persists
    r = client.get("/v1/feeders/1")
    assert r.json()["loaded"] is None


def test_load_with_alias_distributor_pn_resolves_to_canonical(api, client):
    """Guard against the #354 class of bug (see server/routes/openpnp.py's
    docstring): an alias distributor PN must resolve to the same canonical
    part as a direct lookup, via domain.part_registry's alias_index."""
    registry = part_registry.load(api.base_dir)
    registry.parts["C100000"] = ["C100000", "OLD-ALIAS-PN"]
    part_registry.save(api.base_dir, registry)

    client.post("/v1/feeders/1/register", json={"feeder_type": "strip-feeder"})
    r = client.post("/v1/feeders/1/load", json={"part_key": "OLD-ALIAS-PN", "qty": 10})
    assert r.status_code == 200
    assert r.json()["loaded"]["part_key"] == "C100000"


def test_load_derives_tape_width_for_chip_passive(client):
    """Explicit tape_width_mm omitted -> derived from the Tier-1 family
    table for a standard chip-size passive (default seed is a 0402 resistor)."""
    client.post("/v1/feeders/1/register", json={"feeder_type": "strip-feeder"})
    r = client.post("/v1/feeders/1/load", json={"part_key": "C100000", "qty": 10})
    assert r.json()["loaded"]["tape_width_mm"] == 8.0


def test_load_leaves_tape_width_null_for_unmapped_package(tmp_path):
    """An IC (no Tier-1 family match) -> tape_width_mm stays null, no crash."""
    from fastapi.testclient import TestClient

    from server.app import create_app

    inst = make_api(tmp_path)
    write_ledger(inst, [make_part(lcsc="C300000", desc="Microcontroller STM32F103", pkg="LQFP48")])
    with TestClient(create_app(inst)) as c:
        c.post("/v1/feeders/1/register", json={"feeder_type": "strip-feeder"})
        r = c.post("/v1/feeders/1/load", json={"part_key": "C300000", "qty": 5})
        assert r.status_code == 200
        assert r.json()["loaded"]["tape_width_mm"] is None
    inst.shutdown()


def test_load_explicit_tape_width_overrides_derivation(client):
    client.post("/v1/feeders/1/register", json={"feeder_type": "strip-feeder"})
    r = client.post(
        "/v1/feeders/1/load",
        json={"part_key": "C100000", "qty": 10, "tape_width_mm": 12.0},
    )
    assert r.json()["loaded"]["tape_width_mm"] == 12.0


def test_load_nonexistent_part_is_4xx(client):
    client.post("/v1/feeders/1/register", json={"feeder_type": "strip-feeder"})
    r = client.post("/v1/feeders/1/load", json={"part_key": "NO-SUCH-PART", "qty": 10})
    assert r.status_code == 404
    body = r.json()
    assert set(body.keys()) == {"error", "code", "detail"}
    assert body["code"] == "not_found"


def test_unload_before_register_is_4xx(client):
    r = client.post("/v1/feeders/99/unload")
    assert r.status_code == 404


def test_no_credentials_401_in_on_mode(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app import create_app

    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    inst = make_api(tmp_path)
    write_ledger(inst, [make_part(lcsc="C100000", qty=10)])
    REMOTE = ("100.64.1.2", 51234)
    with TestClient(create_app(inst), client=REMOTE) as c:
        r_list = c.get("/v1/feeders")
        r_get = c.get("/v1/feeders/1")
        r_register = c.post("/v1/feeders/1/register", json={"feeder_type": "x"})
        r_sheet = c.get("/v1/feeders/tags/sheet")
    assert r_list.status_code == 401
    assert r_get.status_code == 401
    assert r_register.status_code == 401
    assert r_sheet.status_code == 401
    inst.shutdown()


# ── AprilTag sheet generator ─────────────────────────────────────────────────


def _render_page_gray(page: "fitz.Page", dpi: int = 600) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n >= 3:
        return cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2GRAY)
    return arr[:, :, 0]


def _detect(gray: np.ndarray):
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    params = cv2.aruco.DetectorParameters()
    detector = cv2.aruco.ArucoDetector(d, params)
    return detector.detectMarkers(gray)


def test_sheet_round_trip_detects_requested_id(client):
    """Generate a one-tag sheet, render it, and confirm cv2.aruco detects the
    SAME id — proves the generated tag is a valid, correctly-encoded 36h11
    marker, not just an arbitrary black square."""
    r = client.get("/v1/feeders/tags/sheet", params={"start": 42, "count": 1, "tag_mm": 8.0})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"

    doc = fitz.open(stream=r.content, filetype="pdf")
    assert doc.page_count >= 1
    page = doc[0]

    gray = _render_page_gray(page)
    corners, ids, rejected = _detect(gray)
    assert ids is not None
    assert 42 in ids.flatten().tolist()


def test_sheet_placed_size_matches_tag_mm(client):
    """The placed marker image's bbox width (in points) matches tag_mm at
    72pt/inch, within a small tolerance."""
    tag_mm = 10.0
    r = client.get("/v1/feeders/tags/sheet", params={"start": 5, "count": 1, "tag_mm": tag_mm})
    assert r.status_code == 200

    doc = fitz.open(stream=r.content, filetype="pdf")
    page = doc[0]
    infos = page.get_image_info()
    assert len(infos) == 1
    bbox = infos[0]["bbox"]
    width_pt = bbox[2] - bbox[0]
    height_pt = bbox[3] - bbox[1]
    expected_pt = tag_mm * MM_TO_PT
    assert abs(width_pt - expected_pt) < 0.5
    assert abs(height_pt - expected_pt) < 0.5


def test_sheet_multiple_tags_all_detected(client):
    r = client.get("/v1/feeders/tags/sheet", params={"start": 0, "count": 6, "tag_mm": 8.0})
    assert r.status_code == 200
    doc = fitz.open(stream=r.content, filetype="pdf")
    found = set()
    for page in doc:
        gray = _render_page_gray(page)
        _corners, ids, _rejected = _detect(gray)
        if ids is not None:
            found.update(ids.flatten().tolist())
    assert found == set(range(6))


def test_sheet_id_beyond_dict_capacity_is_400(client):
    r = client.get("/v1/feeders/tags/sheet", params={"start": 586, "count": 5})
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "value_error"


def test_sheet_unknown_page_size_is_400(client):
    r = client.get("/v1/feeders/tags/sheet", params={"page": "tabloid"})
    assert r.status_code == 400


# ── AprilTag single-tag PNG (primary: LabelWorks image import) ─────────────


def test_tag_png_round_trip_detects_requested_id(client):
    """Generate a single-tag PNG and confirm cv2.aruco detects the SAME id —
    proves it's a valid, correctly-encoded 36h11 marker."""
    r = client.get("/v1/feeders/tags/17.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"

    from PIL import Image

    img = Image.open(io.BytesIO(r.content))
    gray = np.array(img.convert("L"))
    corners, ids, _rejected = _detect(gray)
    assert ids is not None
    assert 17 in ids.flatten().tolist()


def test_tag_png_default_size_and_dpi_for_label_tape(client):
    """Default (tag_mm=7.0, dpi=180) targets 12mm tape: black-tag pixel width
    matches tag_mm at the embedded dpi within tolerance, and dpi metadata is
    written into the PNG itself so 'print actual size' reproduces it."""
    from PIL import Image

    r = client.get("/v1/feeders/tags/17.png")
    img = Image.open(io.BytesIO(r.content))
    dpi = img.info.get("dpi")
    assert dpi is not None
    assert abs(dpi[0] - 180) < 1.0
    assert abs(dpi[1] - 180) < 1.0

    gray = np.array(img.convert("L"))
    corners, ids, _rejected = _detect(gray)
    assert ids is not None
    corner = corners[0][0]
    black_px = corner[:, 0].max() - corner[:, 0].min()
    expected_px = 7.0 / 25.4 * 180
    assert abs(black_px - expected_px) < 3.0


def test_tag_png_custom_size_and_dpi(client):
    from PIL import Image

    r = client.get("/v1/feeders/tags/3.png", params={"tag_mm": 10.0, "dpi": 300})
    assert r.status_code == 200
    img = Image.open(io.BytesIO(r.content))
    dpi = img.info.get("dpi")
    assert abs(dpi[0] - 300) < 1.0

    gray = np.array(img.convert("L"))
    corners, ids, _rejected = _detect(gray)
    assert ids is not None and 3 in ids.flatten().tolist()
    corner = corners[0][0]
    black_px = corner[:, 0].max() - corner[:, 0].min()
    expected_px = 10.0 / 25.4 * 300
    assert abs(black_px - expected_px) < 5.0


def test_tag_png_has_white_quiet_zone_border(client):
    """The composed canvas has a white margin around the black tag (>= 1
    module) — corners of the whole image must be pure white, not part of
    the tag pattern."""
    from PIL import Image

    r = client.get("/v1/feeders/tags/17.png", params={"label": False})
    img = Image.open(io.BytesIO(r.content))
    gray = np.array(img.convert("L"))
    assert gray[0, 0] == 255
    assert gray[0, -1] == 255
    assert gray[-1, 0] == 255
    assert gray[-1, -1] == 255


def test_tag_png_label_true_grows_canvas_for_id_text(client):
    from PIL import Image

    r_no_label = client.get("/v1/feeders/tags/17.png", params={"label": False})
    r_label = client.get("/v1/feeders/tags/17.png", params={"label": True})
    img_no_label = Image.open(io.BytesIO(r_no_label.content))
    img_label = Image.open(io.BytesIO(r_label.content))
    assert img_no_label.size[0] == img_label.size[0]
    assert img_label.size[1] > img_no_label.size[1]


def test_tag_png_id_beyond_dict_capacity_is_400(client):
    r = client.get("/v1/feeders/tags/9999.png")
    assert r.status_code == 400
    assert r.json()["code"] == "value_error"


def test_tag_png_negative_id_is_4xx(client):
    r = client.get("/v1/feeders/tags/-1.png")
    assert r.status_code in (400, 404, 422)


def test_tag_png_no_credentials_401_in_on_mode(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app import create_app

    monkeypatch.setenv("DUBIS_AUTH_MODE", "on")
    inst = make_api(tmp_path)
    write_ledger(inst, [make_part(lcsc="C100000", qty=10)])
    REMOTE = ("100.64.1.2", 51234)
    with TestClient(create_app(inst), client=REMOTE) as c:
        r = c.get("/v1/feeders/tags/17.png")
    assert r.status_code == 401
    inst.shutdown()
