"""GPU integration test for the local VLM image-recognition backend.

Marked ``gpu`` and deselected by default (see pyproject ``addopts``). Runs only
on a node with a GPU and a reachable VLM server holding a Qwen2.5-VL model —
i.e. the self-hosted ``gpu`` runner (y740 / RTX 2060), which reaches the
in-cluster **llama.cpp** server
(``llamacpp.win-runners.svc.cluster.local:8080``). No mocks: end-to-end proof
that the node's GPU/server/model stack works and that ``available()`` selects a
usable model.

``available()`` alone is not proof of a working extractor: a live server can
probe fine and still kill the whole path downstream — llama.cpp wraps its reply
in a ```json fence, which used to make ``extract_line_items`` return None and
fall back to Tesseract with only a debug log to show for it. So the second test
actually runs an inference on a synthetically rendered packing list (Pillow, no
committed fixture, no real customer data) and asserts real extracted rows. OCR
and VLM reads are both fuzzy, so it asserts the machine-clean signals — a part
number that came back non-empty and a plausible integer quantity — not every
glyph of every row.
"""
import io

import pytest
from PIL import Image, ImageDraw, ImageFont

import vlm_extract

# Synthetic goods table: LCSC-style catalogue numbers + real MPN spellings, all
# invented order data. No customer/address/PII of any kind appears in the image.
_COLS = [40, 140, 380, 900, 1120]
_HEADERS = ["No.", "LCSC Part #", "Mfr. Part #", "Description", "Qty"]
_ROWS = [
    ("1", "C12624", "CC0402KRX7R7BB104", "100nF 50V X7R 0402 Capacitor", "500"),
    ("2", "C25804", "RC0402FR-0710KL", "10K 1% 1/16W 0402 Resistor", "1000"),
    ("3", "C2286", "KT-0603G", "Emerald Green LED 0603", "250"),
]


def _font(size):
    for name in ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _render_packing_list() -> bytes:
    """Render a clean bordered packing-list table to PNG bytes."""
    ys = [60 + 90 * i for i in range(len(_ROWS) + 2)]
    w, h = 1240, ys[-1] + 60
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    f = _font(24)
    d.text((_COLS[0], 20), "PACKING LIST  -  Order 0000001", fill="black", font=_font(28))
    for x in _COLS + [w - 40]:
        d.line([(x, ys[0]), (x, ys[-1])], fill="black", width=2)
    for y in ys:
        d.line([(_COLS[0], y), (w - 40, y)], fill="black", width=2)

    def put(r, c, text):
        d.text((_COLS[c] + 8, ys[r] + 30), text, fill="black", font=f)

    for c, head in enumerate(_HEADERS):
        put(0, c, head)
    for i, row in enumerate(_ROWS, 1):
        for c, val in enumerate(row):
            put(i, c, val)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.gpu
def test_vlm_backend_live_on_gpu_node():
    # Real model server on this GPU node (no mocks). available() probes
    # /v1/models and selects the best served Qwen2.5-VL model — the 3B is what
    # fits the 6 GB RTX 2060.
    assert vlm_extract.available(), (
        f"VLM backend unavailable on this GPU node — the model server at "
        f"{vlm_extract._base_url()} must be up (GET /v1/models) with a "
        f"Qwen2.5-VL model loaded."
    )
    # After a successful probe, model_name() is the id the server actually
    # reported — available() records it, so this is a real check on the loaded
    # model rather than an echo of the preference list's head.
    selected = vlm_extract.model_name()
    assert selected in (vlm_extract._served_models() or set()), selected
    # Matched by marker, not prefix: llama.cpp reports its --alias
    # ("qwen2.5-vl-3b") while other /v1 servers report a tag ("qwen2.5vl:3b").
    assert vlm_extract._is_qwen_vl(selected), selected


@pytest.mark.gpu
def test_vlm_extracts_line_items_from_a_rendered_packing_list():
    """End-to-end inference: a real image in, real line-item dicts out.

    This is the assertion ``available()`` cannot make. Anything that breaks the
    reply-parsing path (a markdown-fenced reply, a top-level array instead of
    ``{"items": ...}``) shows up here as None, where ``available()`` would still
    be cheerfully green.
    """
    assert vlm_extract.available(), (
        f"VLM backend unavailable on this GPU node — the model server at "
        f"{vlm_extract._base_url()} must be up (GET /v1/models) with a "
        f"Qwen2.5-VL model loaded."
    )
    png = _render_packing_list()
    rows = vlm_extract.extract_line_items(png, "lcsc")
    assert rows, (
        f"VLM returned no line items for the rendered packing list "
        f"(model {vlm_extract.model_name()}). extract_line_items swallows every "
        f"failure into None — check the WARNING log for the real cause; a "
        f"markdown-fenced reply is the usual one."
    )
    # A read this clean should recover most rows; the model may still merge or
    # drop one, so require substantially all of them rather than exactly all.
    assert len(rows) >= len(_ROWS) - 1, rows
    for row in rows:
        assert row["_backend"] == "vlm"
        assert row["distributor"] == "lcsc"
    # At least one row must carry a part number the model actually read, and a
    # quantity that is a plausible integer (not a stray "1" from a row index).
    identified = [r for r in rows if r["distributor_pn"] or r["mpn"]]
    assert identified, rows
    quantities = [r["quantity"] for r in identified]
    assert all(isinstance(q, int) for q in quantities), quantities
    assert any(q >= 100 for q in quantities), rows
