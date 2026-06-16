"""Grid-aware table extraction for bordered packing lists / invoices.

Distributor packing lists (LCSC, DigiKey, …) are ruled tables. Flat OCR reads the
cells in a scrambled order and mangles values where neighbouring text bleeds in
(e.g. an LCSC ``C12624`` becomes ``�12624``). When the document actually has a
drawn grid we can do far better: detect the table, flatten its perspective, find
each cell as a region enclosed by grid lines, and OCR every cell *in isolation* —
then assign cells to columns using the header row, so each value lands in the
right field.

This is best-effort and self-gating: ``extract_line_items`` returns ``None`` when
no usable grid is found (or OpenCV is unavailable), and the caller falls back to
the existing flat-OCR + heuristic-parse path. It never raises for a "bad" image.

Public API:
    extract_line_items(image_bytes, template="generic") -> list[dict] | None
        Line-item dicts share the distributor_profiles shape:
        {mpn, manufacturer, package, description, quantity, unit_price,
         distributor, distributor_pn}.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# A row band must have at least this many detected/expected cells to count as a
# data row (header + data rows of a real table are wide; stray address boxes
# are not).
_MIN_ROW_CELLS = 5
# Minimum table area as a fraction of the image — guards against latching onto a
# tiny incidental rectangle.
_MIN_TABLE_AREA_FRAC = 0.15

# "Mfr. Part#: <MPN>" inside a description cell. The "Mfr." prefix is OPTIONAL —
# OCR frequently mangles it ("nl Pat #: ...", "i f Pat #: ...") while keeping the
# "Part#:" label legible, so anchor on the "Part#:"/"Pat#:" token.
_MFR_PART_RE = re.compile(
    r"(?:(?:Mfr|Mfg)[.,]?\s*)?Pa\w{0,2}\.?\s*#\s*:?\s*([A-Za-z0-9][\w./()\-]{2,40})",
    re.IGNORECASE,
)


def _cv():
    """Import cv2/numpy lazily; return (cv2, np) or (None, None) if unavailable."""
    try:
        import cv2
        import numpy as np
        return cv2, np
    except Exception:
        return None, None


def extract_line_items(image_bytes: bytes, template: str = "generic"):
    """Extract line items from a bordered table image, or None if not applicable.

    Never raises for image/recognition problems — returns None so the caller can
    fall back to the flat-OCR pipeline.
    """
    cv2, np = _cv()
    if cv2 is None:
        return None
    try:
        return _extract(cv2, np, image_bytes, template)
    except Exception as exc:  # pragma: no cover - defensive: any CV/OCR hiccup
        logger.warning("ocr_table extraction failed, falling back: %s", exc)
        return None


# ── Pipeline ──────────────────────────────────────────────────────────────────


def _extract(cv2, np, image_bytes: bytes, template: str):
    import ocr_engine
    ocr_engine.require_tesseract()

    gray = _decode_gray(cv2, np, image_bytes)
    if gray is None:
        return None
    table = _isolate_and_flatten_table(cv2, np, gray)
    if table is None:
        return None
    table = _orient_upright(cv2, np, table)

    cells = _detect_cells(cv2, np, table)
    if not cells:
        return None
    bands = _group_rows(cells)
    # A "wide" band is a table row (header, data, or totals). We don't assume a
    # header is present or detectable — columns are typed by CONTENT below and
    # non-item rows (header/total) are dropped by _row_to_item — so a packing list
    # with a single data row works too.
    data_bands = [b for b in bands if len(b) >= _MIN_ROW_CELLS]
    if not data_bands:
        return None

    columns = _canonical_columns(data_bands)
    if len(columns) < _MIN_ROW_CELLS:
        return None

    grid = _read_grid(cv2, table, data_bands, columns)
    field_by_col = _classify_columns(grid, columns, template)
    if "distributor_pn" not in field_by_col.values() \
            and "description" not in field_by_col.values():
        return None  # couldn't identify the key columns — let the caller fall back
    items = [_row_to_item(r, field_by_col, template) for r in grid]
    items = [it for it in items if it]
    return items or None


def _decode_gray(cv2, np, image_bytes: bytes):
    buf = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
    return img


def _grid_masks(cv2, np, gray):
    """Return (horizontal_lines, vertical_lines) binary masks."""
    h, w = gray.shape
    bw = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 15)
    horiz = cv2.morphologyEx(
        bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, w // 40), 1)))
    vert = cv2.morphologyEx(
        bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, h // 40))))
    return horiz, vert


def _isolate_and_flatten_table(cv2, np, gray):
    """Find the largest grid contour and perspective-warp it to a flat rectangle.

    Returns the warped grayscale table, or None if no sizeable grid is present.
    """
    h, w = gray.shape
    horiz, vert = _grid_masks(cv2, np, gray)
    grid = cv2.dilate(cv2.add(horiz, vert), np.ones((3, 3), np.uint8), iterations=2)
    cnts, _ = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    big = max(cnts, key=cv2.contourArea)
    if cv2.contourArea(big) < _MIN_TABLE_AREA_FRAC * h * w:
        return None
    rect = cv2.minAreaRect(big)
    box = cv2.boxPoints(rect).astype("float32")
    src = _order_quad(np, box)
    (rw, rh) = rect[1]
    out_w, out_h = int(max(rw, rh)), int(min(rw, rh))
    if out_w < 50 or out_h < 50:
        return None
    dst = np.array([[0, 0], [out_w, 0], [out_w, out_h], [0, out_h]], dtype="float32")
    M = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(gray, M, (out_w, out_h))


def _order_quad(np, pts):
    """Order 4 points as [top-left, top-right, bottom-right, bottom-left]."""
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([
        pts[np.argmin(s)], pts[np.argmin(d)],
        pts[np.argmax(s)], pts[np.argmax(d)],
    ], dtype="float32")


def _orient_upright(cv2, np, table):
    """Rotate the warped table so its text is genuinely upright.

    Uses Tesseract OSD, which reports the true page orientation. We can't use a
    plain ``image_to_string`` vote here: full-page OCR silently auto-rotates, so a
    sideways table still "reads" — but per-cell PSM-7 OCR does NOT auto-rotate, so
    the cells would come out as garbage. OSD's ``rotate`` is the clockwise degrees
    needed to make the page upright.
    """
    import pytesseract
    rot_map = {
        90: cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE,
    }
    try:
        osd = pytesseract.image_to_osd(table, output_type=pytesseract.Output.DICT)
        rotate = int(osd.get("rotate", 0)) % 360
    except Exception as exc:
        logger.warning("OSD orientation failed, leaving table as-is: %s", exc)
        return table
    op = rot_map.get(rotate)
    return cv2.rotate(table, op) if op is not None else table


def _detect_cells(cv2, np, table):
    """Return cell rects (x, y, w, h) as regions enclosed by the grid lines."""
    h, w = table.shape
    horiz, vert = _grid_masks(cv2, np, table)
    grid = cv2.dilate(cv2.add(horiz, vert), np.ones((3, 3), np.uint8), iterations=1)
    inv = cv2.bitwise_not(grid)
    n, _lbl, stats, _cent = cv2.connectedComponentsWithStats(inv, connectivity=4)
    cells = []
    for i in range(1, n):
        x, y, cw, ch, area = stats[i]
        if area < 0.0008 * h * w:
            continue
        if cw > 0.9 * w and ch > 0.9 * h:  # the page-background blob
            continue
        if cw < 20 or ch < 12:
            continue
        cells.append((int(x), int(y), int(cw), int(ch)))
    return cells


def _group_rows(cells):
    """Group cells into row bands by their vertical centre."""
    cells = sorted(cells, key=lambda c: c[1] + c[3] / 2)
    bands: list[list] = []
    cur = [cells[0]]
    for c in cells[1:]:
        if abs((c[1] + c[3] / 2) - (cur[-1][1] + cur[-1][3] / 2)) < 25:
            cur.append(c)
        else:
            bands.append(cur)
            cur = [c]
    bands.append(cur)
    return bands


def _canonical_columns(data_bands):
    """Canonical column rects from the widest data band (x0, x1) per column."""
    ref = max(data_bands, key=len)
    ref = sorted(ref, key=lambda c: c[0])
    return [(c[0], c[0] + c[2]) for c in ref]


def _read_grid(cv2, table, data_bands, columns):
    """OCR every cell, assigning to columns; synthesise missing cells by geometry."""
    rows = []
    for band in data_bands:
        y0 = min(c[1] for c in band)
        y1 = max(c[1] + c[3] for c in band)
        row = []
        for (cx0, cx1) in columns:
            # Prefer a detected cell whose centre falls in this column.
            match = None
            for c in band:
                cc = c[0] + c[2] / 2
                if cx0 <= cc <= cx1:
                    match = c
                    break
            if match is not None:
                x, y, cw, ch = match
            else:
                # Missing (faint separator dropped the cell) — crop by geometry.
                x, y, cw, ch = cx0, y0, cx1 - cx0, y1 - y0
            row.append(_ocr_cell(cv2, table, x, y, cw, ch))
        rows.append(row)
    return rows


def _ocr_cell(cv2, table, x, y, cw, ch):
    import pytesseract
    pad = 5
    cell = table[y + pad:y + ch - pad, x + pad:x + cw - pad]
    if cell.size == 0 or min(cell.shape) < 8:
        return ""
    cell = cv2.resize(cell, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    txt = pytesseract.image_to_string(cell, config="--psm 7")
    return txt.strip().replace("\n", " ")


# Distributor PN shapes for content-based column detection.
_PN_PATTERNS = {
    "lcsc": re.compile(r"C\d{2,9}", re.IGNORECASE),
    "digikey": re.compile(r"[A-Z0-9][A-Z0-9.\-]{2,40}-(?:ND|CT|DKR|TR)", re.IGNORECASE),
    "mouser": re.compile(r"\d{2,4}-[A-Z0-9]", re.IGNORECASE),
}
_INT_CELL_RE = re.compile(r"^\s*\d[\d,]*\s*$")


def _classify_columns(grid, columns, template):
    """Identify each column's field from CELL CONTENT, not the (often-garbled)
    header text. The PN column is the one whose cells mostly match the
    distributor's PN shape; the description column is the widest; quantity is the
    first integer-heavy column to the right of the description.

    Classification spans ALL rows (we don't drop a header row): header/total cells
    don't match the PN shape and rarely the integer shape, so they don't mislead
    the typing — and this keeps single-data-row packing lists working."""
    rows = grid
    ncol = len(columns)
    field: dict[int, str] = {}

    pn_re = _PN_PATTERNS.get(template)
    if pn_re is not None:
        counts = [sum(1 for r in rows if j < len(r) and pn_re.search(r[j]))
                  for j in range(ncol)]
        if max(counts, default=0) > 0:
            field[counts.index(max(counts))] = "distributor_pn"

    widths = [x1 - x0 for (x0, x1) in columns]
    desc_idx = max(range(ncol), key=lambda j: widths[j])
    if field.get(desc_idx) != "distributor_pn":
        field[desc_idx] = "description"

    # Quantity = first integer-heavy column to the RIGHT of the description (Qty.
    # Ordered). Threshold is permissive so a single coarse data row still
    # registers; the "No." column can't be picked because it sits left of desc.
    int_frac = [
        (sum(1 for r in rows if j < len(r) and _INT_CELL_RE.match(r[j]))
         / max(1, len(rows)))
        for j in range(ncol)
    ]
    for j in range(desc_idx + 1, ncol):
        if j not in field and int_frac[j] >= 0.3:
            field[j] = "quantity"
            break
    return field


def _to_qty(raw: str) -> int:
    m = re.search(r"\d[\d,]*", raw or "")
    if not m:
        return 0
    try:
        return int(m.group(0).replace(",", ""))
    except ValueError:
        return 0


def _row_to_item(cells, field_by_col, template):
    """Turn one OCR'd row into a line-item dict, or None if it has no PN/MPN."""
    by_field: dict[str, str] = {}
    for idx, text in enumerate(cells):
        field = field_by_col.get(idx)
        if not field:
            continue
        by_field.setdefault(field, text)

    distributor_pn = (by_field.get("distributor_pn") or "").strip()
    pn_re = _PN_PATTERNS.get(template)
    if pn_re is not None:
        m = pn_re.search(distributor_pn)
        distributor_pn = (m.group(0).upper() if template == "lcsc"
                          else m.group(0)) if m else ""

    desc_raw = by_field.get("description") or ""
    mpn = ""
    mm = _MFR_PART_RE.search(desc_raw)
    if mm:
        mpn = mm.group(1).strip().rstrip("_-./ ")
    description = _MFR_PART_RE.sub("", desc_raw).strip(" -|") if desc_raw else ""

    quantity = _to_qty(by_field.get("quantity", ""))

    if not distributor_pn and not mpn:
        return None
    return {
        "mpn": mpn,
        "manufacturer": "",
        "package": "",
        "description": description,
        "quantity": quantity,
        "unit_price": 0.0,
        "distributor": template if template != "generic" else "generic",
        "distributor_pn": distributor_pn,
    }
