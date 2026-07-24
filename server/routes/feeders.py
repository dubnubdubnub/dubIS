"""Loading-station /v1/feeders routes: feeder identity + reel binding, plus
an AprilTag 36h11 print-at-100% sheet generator.

This is the LOADING STATION side of the dubIS <-> OpenPnP bridge (see
docs/plans/phase3a-openpnp-bridge-design.md's "Feeder identity" section) — a
workstation separate from the (currently offline) PnP machine. An operator
prints AprilTags here, registers tag -> feeder, and binds a part PICKED IN
DUBIS (no barcode scanning — the part is selected via dubIS's own
search/inventory, not read off a physical label) to a feeder as a loaded
reel.

## Identity resolution (the #354 guard)

`load_feeder_reel` resolves `part_key` through the SAME canonical-identity
path as `domain/part_registry.py` / `server/routes/openpnp.py`'s
`get_openpnp_part` (the alias-index registry) rather than inventing a second,
parallel key derivation — the exact bug class fixed in PR #354
(`get_sourced_distributors`'s "loose match" key scope drifting from
`update_part_fields`'s strict `get_part_key`).

## Tape-width auto-derivation

When `tape_width_mm` is omitted, it's derived from the part's package via the
same Tier-1 family table (`data/openpnp_families.json`) `get_openpnp_part`
uses: any standard chip-passive size (0201/0402/0603/0805/1206) maps to 8mm
tape (the standard reel width for those sizes); anything else (ICs,
connectors, oddball/non-chip parts) leaves `tape_width_mm` as `null` rather
than guessing.

## AprilTag generators — PNG (primary) + PDF sheet (fallback)

The primary output is `GET /v1/feeders/tags/{tag_id}.png` — a SINGLE AprilTag
36h11 PNG sized for label tape, meant to be imported as an image into Epson
LabelWorks (dubIS's own Epson CSV export is text-only and can't carry a
raster, so this is a separate image the operator imports directly in
LabelWorks). The marker (`cv2.aruco.generateImageMarker`) is rendered at a
crisp, integer-module-multiple resolution, then composed onto a white canvas
with a white quiet zone (>= 1 module) around the black tag; the canvas is
sized so the black tag is EXACTLY `tag_mm` millimeters at the embedded `dpi`
(`black_px = round(tag_mm/25.4*dpi)`), and that `dpi` is written into the
PNG's own metadata (`PIL.Image.save(..., dpi=(dpi, dpi))`) so "print actual
size" in any viewer reproduces the physical size. Defaults (`tag_mm=7.0`,
`dpi=180`) target 12mm label tape: a 7mm tag + ~1mm quiet zone on each side
fits the ~9mm usable printable height LabelWorks leaves on 12mm tape.
`label=true` (default) also draws the numeric tag id as small text next to
the marker, to help the operator identify tags by eye.

`GET /v1/feeders/tags/sheet` is a FALLBACK for a normal sheet printer: it
tiles `count` markers into a print-at-100% PDF (PyMuPDF/fitz), each placed at
EXACTLY `tag_mm` millimeters (mm -> pt: `pt = mm/25.4*72`) with its own white
quiet-zone border and an ID label below.

Both are fetched directly by the browser as binary responses — deliberately
excluded from the api-map (see `scripts/gen-api-client.py`'s
`SKIP_OPERATION_IDS`).
"""

from __future__ import annotations

import io
from datetime import UTC, datetime

import cv2
import fitz
import numpy as np
from fastapi import APIRouter, Query, Request, Response
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel

from domain import feeders, part_registry
from server.models import FeederListResponse, FeederModel
from server.routes.openpnp import _SECTION_TO_PART_TYPE, _find_item, _load_families, _size_code

router = APIRouter(prefix="/v1/feeders", tags=["feeders"])

# ── AprilTag 36h11 shared marker rendering ──────────────────────────────────

_APRILTAG_DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
# DICT_APRILTAG_36h11 has exactly 587 markers (ids 0..586) — see
# cv2.aruco.getPredefinedDictionary(...).bytesList.shape[0].
MAX_TAG_ID = _APRILTAG_DICT.bytesList.shape[0] - 1

# generateImageMarker's default borderBits=1 on each side of the dictionary's
# own data-bit grid (markerSize=6 for 36h11) -> 8 total modules across.
_TOTAL_MODULES = _APRILTAG_DICT.markerSize + 2

MM_TO_PT = 72.0 / 25.4
MM_TO_IN = 1.0 / 25.4

# LabelWorks-typical printer DPI (also commonly 360).
DEFAULT_TAG_DPI = 180
# Default tag size targets 12mm tape: 7mm tag + ~1mm quiet zone each side
# fits the ~9mm usable printable height LabelWorks leaves on 12mm tape.
DEFAULT_TAG_MM = 7.0


def _validate_tag_id(tag_id: int) -> None:
    if not (0 <= tag_id <= MAX_TAG_ID):
        raise ValueError(
            f"Tag id {tag_id} is outside DICT_APRILTAG_36h11's range "
            f"(0..{MAX_TAG_ID})",
        )


def _render_marker_array(tag_id: int, black_px: int) -> np.ndarray:
    """A crisp black_px x black_px binary marker array for *tag_id*.

    Rendered first at an INTEGER multiple of the dictionary's own module
    count (clean module edges, no interpolation artifacts within a module),
    then resized to the exact requested pixel size and re-binarized so
    resizing never leaves soft/gray edges that could confuse detection.
    """
    module_px = 40
    master_px = _TOTAL_MODULES * module_px
    master = cv2.aruco.generateImageMarker(_APRILTAG_DICT, tag_id, master_px)
    if black_px == master_px:
        return master
    interp = cv2.INTER_AREA if black_px < master_px else cv2.INTER_NEAREST
    resized = cv2.resize(master, (black_px, black_px), interpolation=interp)
    _, binarized = cv2.threshold(resized, 127, 255, cv2.THRESH_BINARY)
    return binarized

# Physical page sizes in millimeters.
_PAGE_SIZES_MM = {
    "letter": (215.9, 279.4),
    "a4": (210.0, 297.0),
}

# Fixed marker render resolution in pixels (scaled to the exact `tag_mm`
# physical size at placement time — this only controls print sharpness, not
# the placed size, since fitz draws the raster into an exact-points rect).
_MARKER_PX = 480

_PAGE_MARGIN_PT = 36.0  # 0.5in
_LABEL_HEIGHT_PT = 10.0
_LABEL_FONTSIZE = 7.0


def _marker_png_bytes(tag_id: int, px: int) -> bytes:
    img = cv2.aruco.generateImageMarker(_APRILTAG_DICT, tag_id, px)
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError(f"failed to encode AprilTag marker {tag_id}")
    return buf.tobytes()


def _build_sheet_pdf(start: int, count: int, tag_mm: float, page_key: str) -> bytes:
    page_w_mm, page_h_mm = _PAGE_SIZES_MM[page_key]
    page_w_pt = page_w_mm * MM_TO_PT
    page_h_pt = page_h_mm * MM_TO_PT

    tag_pt = tag_mm * MM_TO_PT
    # >= 1 AprilTag module of white quiet zone on every side of the placed
    # tag, so neighboring tags/labels never touch it (required for reliable
    # detection).
    quiet_pt = tag_pt / 8.0

    cell_w = tag_pt + 2 * quiet_pt
    cell_h = tag_pt + 2 * quiet_pt + _LABEL_HEIGHT_PT

    usable_w = page_w_pt - 2 * _PAGE_MARGIN_PT
    usable_h = page_h_pt - 2 * _PAGE_MARGIN_PT
    cols = max(1, int(usable_w // cell_w))
    rows_per_page = max(1, int(usable_h // cell_h))
    per_page = cols * rows_per_page

    doc = fitz.open()
    page = None
    for i in range(count):
        tag_id = start + i
        idx_on_page = i % per_page
        if idx_on_page == 0:
            page = doc.new_page(width=page_w_pt, height=page_h_pt)
        col = idx_on_page % cols
        row = idx_on_page // cols

        cell_x0 = _PAGE_MARGIN_PT + col * cell_w
        cell_y0 = _PAGE_MARGIN_PT + row * cell_h
        x0 = cell_x0 + quiet_pt
        y0 = cell_y0 + quiet_pt
        tag_rect = fitz.Rect(x0, y0, x0 + tag_pt, y0 + tag_pt)

        png_bytes = _marker_png_bytes(tag_id, _MARKER_PX)
        page.insert_image(tag_rect, stream=png_bytes)

        label_rect = fitz.Rect(
            cell_x0, y0 + tag_pt, cell_x0 + cell_w, y0 + tag_pt + _LABEL_HEIGHT_PT,
        )
        page.insert_textbox(label_rect, str(tag_id), fontsize=_LABEL_FONTSIZE, align=1)

    return doc.tobytes()


@router.get("/tags/sheet", operation_id="get_feeder_tag_sheet")
def get_feeder_tag_sheet(
    request: Request,
    start: int = Query(0, ge=0),
    count: int = Query(24, ge=1, le=500),
    tag_mm: float = Query(8.0, gt=0),
    page: str = "letter",
) -> Response:
    if page not in _PAGE_SIZES_MM:
        raise ValueError(
            f"Unknown page size {page!r}; choose one of {sorted(_PAGE_SIZES_MM)}",
        )
    end_id = start + count - 1
    if end_id > MAX_TAG_ID:
        raise ValueError(
            f"Requested tag ids {start}..{end_id} exceed DICT_APRILTAG_36h11's "
            f"capacity (max id {MAX_TAG_ID})",
        )

    pdf_bytes = _build_sheet_pdf(start, count, tag_mm, page)
    return Response(content=pdf_bytes, media_type="application/pdf")


# ── AprilTag single-tag PNG (primary: LabelWorks image import) ─────────────

_PNG_LABEL_FONT_RATIO = 0.09  # label font size as a fraction of dpi (px)
_PNG_LABEL_GAP_RATIO = 0.03   # gap between quiet zone and label, as a fraction of dpi


def _build_tag_png(tag_id: int, tag_mm: float, dpi: int, label: bool) -> bytes:
    black_px = max(_TOTAL_MODULES, round(tag_mm * MM_TO_IN * dpi))
    quiet_px = max(1, round(black_px / _TOTAL_MODULES))

    tag_img = _render_marker_array(tag_id, black_px)

    canvas_w = black_px + 2 * quiet_px
    canvas_h = canvas_w
    gap_px = 0
    font = None
    text = str(tag_id)
    if label:
        font_size = max(6, round(dpi * _PNG_LABEL_FONT_RATIO))
        try:
            font = ImageFont.load_default(size=font_size)
        except TypeError:  # older Pillow: load_default() takes no args
            font = ImageFont.load_default()
        gap_px = max(1, round(dpi * _PNG_LABEL_GAP_RATIO))
        # Reserve space with a throwaway draw context to measure the text.
        probe = Image.new("L", (1, 1))
        bbox = ImageDraw.Draw(probe).textbbox((0, 0), text, font=font)
        canvas_h += gap_px + (bbox[3] - bbox[1])

    canvas = np.full((canvas_h, canvas_w), 255, dtype=np.uint8)
    canvas[quiet_px:quiet_px + black_px, quiet_px:quiet_px + black_px] = tag_img

    img = Image.fromarray(canvas, mode="L")
    if label:
        draw = ImageDraw.Draw(img)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        tx = max(0, (canvas_w - text_w) // 2)
        ty = canvas_w + gap_px
        draw.text((tx, ty), text, fill=0, font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG", dpi=(dpi, dpi))
    return buf.getvalue()


@router.get("/tags/{tag_id}.png", operation_id="get_feeder_tag_png")
def get_feeder_tag_png(
    request: Request,
    tag_id: int,
    tag_mm: float = Query(DEFAULT_TAG_MM, gt=0),
    dpi: int = Query(DEFAULT_TAG_DPI, gt=0),
    label: bool = Query(True),
) -> Response:
    _validate_tag_id(tag_id)
    png_bytes = _build_tag_png(tag_id, tag_mm, dpi, label)
    return Response(content=png_bytes, media_type="image/png")


# ── Feeder entity CRUD ───────────────────────────────────────────────────────


class RegisterFeederBody(BaseModel):
    feeder_type: str


class LoadFeederBody(BaseModel):
    part_key: str
    qty: int
    tape_width_mm: float | None = None


def _to_response(tag_id: str, record: dict) -> dict:
    return {"tag_id": tag_id, **record}


def _derive_tape_width_mm(item: dict) -> float | None:
    """Standard tape width for a chip passive, via the same Tier-1 family
    table `get_openpnp_part` uses. All standard chip sizes (0201/0402/0603/
    0805/1206) use 8mm tape; anything not in the table -> None (no guess)."""
    families = _load_families()
    top_section = (item.get("section") or "").split(" > ", 1)[0]
    part_type = _SECTION_TO_PART_TYPE.get(top_section)
    package_raw = (item.get("package") or "").strip()
    size_code = _size_code(package_raw)
    if not part_type or not size_code:
        return None
    if f"{part_type}_{size_code}" not in families:
        return None
    return 8.0


@router.get("", response_model=FeederListResponse, operation_id="list_feeders")
def list_feeders(request: Request) -> dict:
    api = request.app.state.api
    store = feeders.load(api.base_dir)
    items = [
        _to_response(tag_id, record)
        for tag_id, record in sorted(feeders.list_all(store).items())
    ]
    return {"feeders": items}


@router.get("/{tag_id}", response_model=FeederModel, operation_id="get_feeder")
def get_feeder(request: Request, tag_id: str) -> dict:
    api = request.app.state.api
    store = feeders.load(api.base_dir)
    record = feeders.get(store, tag_id)
    if record is None:
        raise KeyError(f"Unknown feeder tag: {tag_id}")
    return _to_response(tag_id, record)


@router.post("/{tag_id}/register", response_model=FeederModel, operation_id="register_feeder")
def register_feeder(request: Request, tag_id: str, body: RegisterFeederBody) -> dict:
    api = request.app.state.api
    store = feeders.load(api.base_dir)
    record = feeders.register(store, tag_id, body.feeder_type)
    feeders.save(api.base_dir, store)
    return _to_response(tag_id, record)


@router.post("/{tag_id}/load", response_model=FeederModel, operation_id="load_feeder_reel")
def load_feeder_reel(request: Request, tag_id: str, body: LoadFeederBody) -> dict:
    if body.qty < 0:
        raise ValueError("qty must be >= 0")

    api = request.app.state.api
    registry = part_registry.load(api.base_dir)
    canonical = registry.alias_index.get(body.part_key, body.part_key)

    item = _find_item(api, canonical, body.part_key)
    if item is None:
        raise KeyError(f"Unknown part: {body.part_key}")

    tape_width_mm = body.tape_width_mm
    if tape_width_mm is None:
        tape_width_mm = _derive_tape_width_mm(item)

    store = feeders.load(api.base_dir)
    loaded_at = datetime.now(UTC).isoformat()
    record = feeders.load_reel(
        store, tag_id, canonical, body.qty, loaded_at, tape_width_mm=tape_width_mm,
    )
    feeders.save(api.base_dir, store)
    return _to_response(tag_id, record)


@router.post("/{tag_id}/unload", response_model=FeederModel, operation_id="unload_feeder")
def unload_feeder(request: Request, tag_id: str) -> dict:
    api = request.app.state.api
    store = feeders.load(api.base_dir)
    record = feeders.unload(store, tag_id)
    feeders.save(api.base_dir, store)
    return _to_response(tag_id, record)
