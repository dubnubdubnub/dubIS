"""PnP consumption server — HTTP API for OpenPnP to report part placements.

Also hosts the phone-facing "Scan a PO with your phone" capture flow: a small
in-memory session registry, a mobile-friendly HTML capture page, and an upload
endpoint that OCRs the photo and pushes results to the desktop UI.
"""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from pnp_part_map import _load_part_map, _resolve_part_id  # noqa: F401
from scan_capture_page import _capture_page_html, _expired_page_html  # noqa: F401
from scan_image import (  # noqa: F401
    SCAN_IMAGE_EXTS,
    SCAN_MAX_IMAGE_BYTES,
    SCAN_MAX_IMAGES,
    _normalize_groups,
    _save_scan_image,
    _ScanUploadError,
    _validate_scan_image,
)
from scan_sessions import SCAN_SESSION_TTL, _get_scan_session, create_scan_session  # noqa: F401

logger = logging.getLogger(__name__)

PNP_PORT = 7890


class PnPHandler(BaseHTTPRequestHandler):
    """HTTP request handler for PnP consumption events."""

    # Idle sockets must not pin a worker thread forever. iOS Safari opens
    # speculative "preconnect" TCP connections that may never send a request
    # line; without a timeout, readline() on such a socket blocks indefinitely.
    timeout = 10

    def log_message(self, format, *args):
        """Route request logging through Python logging instead of stderr."""
        logger.info("PnP server: " + format, *args)

    def _send_json(self, status, data):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def _send_html(self, status, body):
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        parts = urlsplit(self.path)
        route = parts.path
        if route == "/api/health":
            self._send_json(200, {"ok": True})
        elif route == "/api/scan/health":
            self._send_json(200, {"ok": True})
        elif route == "/scan":
            self._handle_scan_page(parse_qs(parts.query))
        elif route == "/api/parts":
            try:
                inventory = self.server.api._load_organized()
                self._send_json(200, {"ok": True, "parts": inventory})
            except Exception as exc:
                logger.error("PnP /api/parts failed: %s", exc)
                self._send_json(500, {"ok": False, "error": str(exc)})
        else:
            self._send_json(404, {"ok": False, "error": "Not found"})

    def _handle_scan_page(self, query):
        session_id = (query.get("s") or [""])[0]
        session = _get_scan_session(self.server, session_id)
        if session is None:
            logger.info("Scan page requested for unknown/expired session")
            self._send_html(404, _expired_page_html())
            return
        self._send_html(200, _capture_page_html(session["template"], session_id))

    def _handle_scan_upload(self, query):
        session_id = (query.get("s") or [""])[0]
        session = _get_scan_session(self.server, session_id)

        # Parse Content-Length defensively; a non-numeric header must not escape
        # the handler thread.
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            length = 0

        # Reject oversized requests BEFORE reading the whole body into memory.
        # A multi-photo upload can carry several images; bound the early reject
        # by the per-image cap × the max image count (plus base64/JSON overhead).
        if length > SCAN_MAX_IMAGES * SCAN_MAX_IMAGE_BYTES * 2:
            # Don't read the (claimed) huge body — that's the whole point of the
            # early reject. Close the connection instead of trying to drain it.
            self.close_connection = True
            self._send_json(413, {"ok": False, "error": "Upload too large"})
            return

        # Always drain the body so the connection isn't reset mid-request.
        raw = self.rfile.read(length) if length else b""

        if session is None:
            self._send_json(404, {"ok": False, "error": "Unknown or expired scan session"})
            return

        try:
            body = json.loads(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"ok": False, "error": f"Bad JSON: {exc}"})
            return

        # Accept either a multi-image upload ({"images": [...]}) or a single
        # legacy image ({"image_b64", "filename"}) for backward compatibility.
        raw_images = body.get("images")
        if not isinstance(raw_images, list):
            raw_images = [{"image_b64": body.get("image_b64") or "",
                           "filename": body.get("filename") or ""}]
        if not raw_images:
            self._send_json(400, {"ok": False, "error": "image_b64 is required"})
            return
        if len(raw_images) > SCAN_MAX_IMAGES:
            self._send_json(413, {
                "ok": False,
                "error": f"Too many images (max {SCAN_MAX_IMAGES})",
            })
            return

        # Validate + decode every image before doing any work; surface the first
        # problem with the same 400/413 semantics as the single-image path.
        try:
            images = [_validate_scan_image(e) for e in raw_images]
        except _ScanUploadError as exc:
            self._send_json(exc.status, {"ok": False, "error": exc.message})
            return

        api = self.server.api
        window = self.server.window
        template = session["template"]

        # Persist every raw photo to data/scans/ immediately — before OCR — so
        # the upload is never lost even if OCR fails or the import is abandoned.
        saved = False
        try:
            for img in images:
                saved_path = _save_scan_image(api.base_dir, img["decoded"], img["ext"])
                logger.info("Scan upload saved to %s", saved_path)
            saved = True
        except OSError as exc:
            logger.error("Failed to save scan upload to disk: %s", exc)

        # Instant desktop acknowledgement: tell the UI the photo(s) arrived
        # BEFORE the (slower) OCR pass, so the user sees feedback the moment the
        # upload lands instead of waiting out OCR. Best-effort — never blocks.
        try:
            window.evaluate_js(
                "window._scanReceiving && window._scanReceiving("
                + json.dumps({"filename": images[0]["filename"],
                              "template": template, "count": len(images)})
                + ")"
            )
        except Exception as exc:
            logger.warning("Scan 'receiving' UI push failed (window may be closed): %s", exc)

        # OCR each image INDEPENDENTLY and keep the per-photo results separate so
        # the desktop can group photos into POs (each group → one PO). Also build
        # flat concatenations for the single-PO back-compat fields + phone verdict.
        photos = []
        all_pages = []
        all_rows = []
        try:
            for idx, img in enumerate(images):
                overlay = api.ocr_overlay_b64(img["image_b64"], img["filename"], template)
                pg = overlay.get("pages") or []
                rows = overlay.get("prefill_rows") or []
                photos.append({
                    "index": idx,
                    "filename": img["filename"],
                    "image_b64": img["image_b64"],
                    "pages": pg,
                    "prefill_rows": rows,
                })
                all_pages.extend(pg)
                all_rows.extend(rows)
        except Exception as exc:
            logger.error("Scan OCR failed: %s", exc)
            self._send_json(500, {"ok": False, "error": f"OCR failed: {exc}"})
            return

        # The grouping the desktop opens its editor with (explicit from the phone,
        # else one PO per photo).
        groups = _normalize_groups(body.get("groups"), len(images))

        # line_items mirrors prefill_rows so the legacy flat-staging branch still
        # has data if `pages` were ever absent on the frontend.
        line_items = all_rows
        payload = {
            # Back-compat single-PO fields (concatenation across all photos).
            "pages": all_pages,
            "prefill_rows": all_rows,
            "line_items": line_items,
            "image_b64": images[0]["image_b64"],
            "filename": images[0]["filename"],
            "template": template,
            "image_count": len(images),
            # Multi-photo grouping: per-photo OCR + the grouping to start from.
            "photos": photos,
            "groups": groups,
        }
        try:
            window.evaluate_js("window._scanReceived(" + json.dumps(payload) + ")")
        except Exception as exc:
            logger.warning("Scan UI push failed (window may be closed): %s", exc)

        logger.info(
            "Scan upload OCR'd %d line item(s) from %d image(s) into %d order(s) (template=%s)",
            len(line_items), len(images), len(groups), template)
        # Return the OCR result to the PHONE too, so it can overlay the detected
        # token boxes on the photo it just captured and show a verdict. Drop each
        # page's image_b64 (the phone already holds the photo) to keep it lean.
        phone_pages = [
            {k: v for k, v in pg.items() if k != "image_b64"} for pg in all_pages
        ]
        # Per-order item counts for the phone verdict ("N orders · M items").
        group_counts = [
            sum(len(photos[i]["prefill_rows"]) for i in grp) for grp in groups
        ]
        self._send_json(200, {
            "ok": True,
            "count": len(line_items),
            "saved": saved,
            "images": len(images),
            "orders": len(groups),
            "group_counts": group_counts,
            "pages": phone_pages,
            "prefill_rows": all_rows,
        })

    def do_POST(self):
        parts = urlsplit(self.path)
        route = parts.path
        if route == "/api/scan/upload":
            self._handle_scan_upload(parse_qs(parts.query))
            return
        if route != "/api/consume":
            # Drain request body to prevent ConnectionResetError on macOS
            length = int(self.headers.get("Content-Length", 0))
            if length:
                self.rfile.read(length)
            self._send_json(404, {"ok": False, "error": "Not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"ok": False, "error": f"Bad JSON: {exc}"})
            return

        try:
            part_id = body.get("part_id", "").strip()
            qty = int(body.get("qty", 1))
        except (ValueError, TypeError, AttributeError) as exc:
            self._send_json(400, {"ok": False, "error": f"Bad request: {exc}"})
            return
        if not part_id:
            self._send_json(400, {"ok": False, "error": "part_id is required"})
            return
        if qty <= 0:
            self._send_json(400, {"ok": False, "error": "qty must be positive"})
            return

        api = self.server.api
        window = self.server.window

        # Resolve part ID
        part_map = _load_part_map(api.base_dir)
        inventory = api._load_organized()
        part_key = _resolve_part_id(part_id, part_map, inventory)

        if not part_key:
            msg = f"Unknown part ID: {part_id}"
            logger.warning("PnP consume: %s", msg)
            self._send_json(404, {"ok": False, "error": msg})
            return

        # Perform the adjustment
        source = getattr(self.server, "source", "openpnp")
        try:
            fresh = api.adjust_part("remove", part_key, qty, "OpenPnP placement", source=source)
        except Exception as exc:
            logger.error("PnP consume adjust_part failed: %s", exc)
            self._send_json(500, {"ok": False, "error": str(exc)})
            return

        # Find new quantity for this part
        new_qty = None
        for item in fresh:
            item_key = item.get("lcsc") or item.get("mpn") or item.get("digikey")
            if item_key == part_key:
                new_qty = item.get("qty")
                break

        # Push UI update
        detail = {"part_id": part_id, "part_key": part_key, "qty": qty, "new_qty": new_qty}
        try:
            js_code = "window._pnpConsume({inv}, {detail})".format(
                inv=json.dumps(fresh),
                detail=json.dumps(detail),
            )
            window.evaluate_js(js_code)
        except Exception as exc:
            logger.warning("PnP UI push failed (window may be closed): %s", exc)

        logger.info("PnP consumed %dx %s (key=%s, new_qty=%s)", qty, part_id, part_key, new_qty)
        self._send_json(200, {
            "ok": True,
            "part_key": part_key,
            "new_qty": new_qty,
        })


class _FastHTTPServer(ThreadingHTTPServer):
    """Threaded HTTPServer that skips the slow FQDN reverse-DNS lookup in server_bind().

    Threaded (one worker thread per connection) so a single slow or idle client
    connection can't head-of-line block every other request on the accept loop.
    iOS Safari routinely opens speculative preconnect sockets that send no
    request; on the old single-threaded server those stalled the page load for
    seconds. Daemon threads so they never block process teardown.
    """

    daemon_threads = True

    def server_bind(self):
        if self.allow_reuse_address and hasattr(self.socket, 'setsockopt'):
            import socket
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()
        self.server_name = self.server_address[0]
        self.server_port = self.server_address[1]


def start_pnp_server(api, window, port=PNP_PORT, source="openpnp"):
    """Start the PnP HTTP server in a daemon thread.

    Returns the running server on success, or None if the port is unavailable
    (e.g. another dubIS instance already bound it). Callers MUST handle None.
    """
    try:
        server = _FastHTTPServer(("0.0.0.0", port), PnPHandler)
    except OSError as exc:
        logger.warning(
            "PnP server disabled — port %d unavailable "
            "(another dubIS instance already running?): %s",
            port, exc,
        )
        return None
    server.api = api
    server.window = window
    server.source = source

    # poll_interval=0.05 (vs the 0.5s default): server.shutdown() in stop_pnp_server
    # blocks until serve_forever's loop next checks the stop flag, which it only does
    # once per poll interval. The default 0.5s dominated app close (~280ms median);
    # 0.05s makes graceful shutdown near-instant at a negligible idle-wakeup cost.
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.05), name="pnp-server", daemon=True,
    )
    thread.start()
    logger.info("PnP server listening on port %d", port)
    return server


def stop_pnp_server(server):
    """Gracefully stop a PnP server returned by start_pnp_server().

    Null-safe (no-op if server is None). shutdown() must be called from a
    thread other than the one running serve_forever(); it blocks until any
    in-flight request finishes (the graceful part), then server_close() frees
    the socket. Best-effort: failures are logged, never raised, so process
    teardown can proceed.
    """
    if server is None:
        return
    try:
        server.shutdown()
        server.server_close()
    except Exception as exc:
        logger.warning("Error stopping PnP server: %s", exc)
