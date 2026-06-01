"""PnP consumption server — HTTP API for OpenPnP to report part placements.

Also hosts the phone-facing "Scan a PO with your phone" capture flow: a small
in-memory session registry, a mobile-friendly HTML capture page, and an upload
endpoint that OCRs the photo and pushes results to the desktop UI.
"""

import base64
import binascii
import html
import json
import logging
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

logger = logging.getLogger(__name__)

PNP_PORT = 7890

# Scan session time-to-live (seconds). A QR code / capture page is only useful
# for a short window; expire sessions so stale ids can't be replayed.
SCAN_SESSION_TTL = 15 * 60

# Max accepted decoded image size for an upload (bytes). Phone photos are a few
# MB; 15 MB gives generous headroom while rejecting abuse.
SCAN_MAX_IMAGE_BYTES = 15 * 1024 * 1024

# Filename extensions accepted for scan uploads.
SCAN_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def _load_part_map(base_dir):
    """Load pnp_part_map.json from data directory."""
    path = os.path.join(base_dir, "pnp_part_map.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _resolve_part_id(part_id, part_map, inventory):
    """Resolve an OpenPnP part ID to a dubIS part key.

    Strategy:
    1. Check pnp_part_map.json for explicit mapping
    2. Try direct match against inventory LCSC/MPN/Digikey keys
    3. Return None if unresolved
    """
    # 1. Explicit mapping
    if part_id in part_map:
        return part_map[part_id]

    # 2. Direct match against inventory keys
    for item in inventory:
        if part_id in (item.get("lcsc"), item.get("mpn"), item.get("digikey")):
            return item.get("lcsc") or item.get("mpn") or item.get("digikey")

    return None


# ── Scan session registry ──
#
# Stored on the server object as ``server._scan_sessions`` (a dict keyed by
# session id). No background thread: expired sessions are pruned lazily on every
# access. Created by inventory_api.start_scan_session().


def _scan_sessions(server):
    """Return (creating if needed) the server's scan-session registry."""
    registry = getattr(server, "_scan_sessions", None)
    if registry is None:
        registry = {}
        server._scan_sessions = registry
    return registry


def _prune_scan_sessions(server, now=None):
    """Drop sessions older than SCAN_SESSION_TTL. Returns the live registry."""
    now = time.time() if now is None else now
    registry = _scan_sessions(server)
    expired = [
        sid for sid, s in registry.items()
        if now - s.get("created", 0) > SCAN_SESSION_TTL
    ]
    for sid in expired:
        del registry[sid]
    return registry


def create_scan_session(server, template):
    """Register a new scan session on *server* and return its id.

    Used by inventory_api.start_scan_session(); kept here so the registry lives
    next to the routes that consume it.
    """
    registry = _prune_scan_sessions(server)
    session_id = secrets.token_urlsafe(16)
    registry[session_id] = {"template": template, "created": time.time()}
    return session_id


def _get_scan_session(server, session_id):
    """Return the live session dict for *session_id*, or None if missing/expired."""
    if not session_id:
        return None
    registry = _prune_scan_sessions(server)
    return registry.get(session_id)


def _capture_page_html(template, session_id):
    """Build the mobile capture page. All CSS/JS inline; no external assets."""
    t = html.escape(template)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>dubIS — Scan a PO</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 0;
    padding: 24px; line-height: 1.5; }}
  h1 {{ font-size: 1.3rem; margin: 0 0 4px; }}
  .tmpl {{ color: #666; font-size: 0.9rem; margin-bottom: 24px; }}
  label.btn, button {{ display: block; width: 100%; padding: 18px;
    font-size: 1.1rem; text-align: center; border-radius: 12px; border: none;
    margin-bottom: 16px; cursor: pointer; }}
  label.btn {{ background: #2563eb; color: #fff; }}
  label.btn.secondary {{ background: transparent; color: #2563eb;
    border: 1px solid #2563eb; }}
  button.send {{ background: #16a34a; color: #fff; }}
  button:disabled {{ opacity: 0.5; }}
  input[type=file] {{ display: none; }}
  #preview {{ max-width: 100%; border-radius: 12px; margin-bottom: 16px;
    display: none; }}
  .msg {{ padding: 14px; border-radius: 10px; margin-bottom: 16px;
    display: none; }}
  .msg.ok {{ background: #dcfce7; color: #166534; display: block; }}
  .msg.err {{ background: #fee2e2; color: #991b1b; display: block; }}
  .hint {{ font-size: 0.85rem; color: #666; }}
</style>
</head>
<body>
<h1>Scan a Purchase Order</h1>
<div class="tmpl">Template: <strong>{t}</strong></div>

<div id="msg" class="msg"></div>

<label class="btn" for="file">Take a photo</label>
<input id="file" type="file" accept="image/*" capture="environment">

<label class="btn secondary" for="file-library">Upload an existing photo</label>
<input id="file-library" type="file" accept="image/*">

<img id="preview" alt="preview">
<button id="send" class="send" disabled>Send to desktop</button>

<p class="hint">Take a clear photo of the printed purchase order — or upload one
you already have — then tap <em>Send to desktop</em>.</p>

<script>
(function () {{
  var SESSION = {json.dumps(session_id)};
  var cameraInput = document.getElementById("file");
  var libraryInput = document.getElementById("file-library");
  var preview = document.getElementById("preview");
  var sendBtn = document.getElementById("send");
  var msg = document.getElementById("msg");
  var dataUrl = null, filename = null;

  function show(kind, text) {{
    msg.className = "msg " + kind;
    msg.textContent = text;
  }}
  function fallback() {{
    show("err", "Couldn't reach the app. Save the photo and use "
      + "'Choose a file' on the desktop instead.");
  }}

  function handleSelection(input) {{
    var f = input.files && input.files[0];
    if (!f) return;
    filename = f.name || "scan.jpg";
    var reader = new FileReader();
    reader.onload = function () {{
      dataUrl = reader.result;
      preview.src = dataUrl;
      preview.style.display = "block";
      sendBtn.disabled = false;
      msg.className = "msg";
    }};
    reader.onerror = function () {{ show("err", "Could not read the photo."); }};
    reader.readAsDataURL(f);
  }}

  cameraInput.addEventListener("change", function () {{ handleSelection(cameraInput); }});
  libraryInput.addEventListener("change", function () {{ handleSelection(libraryInput); }});

  sendBtn.addEventListener("click", function () {{
    if (!dataUrl) return;
    sendBtn.disabled = true;
    show("ok", "Sending…");
    var comma = dataUrl.indexOf(",");
    var b64 = comma >= 0 ? dataUrl.slice(comma + 1) : dataUrl;
    fetch("/api/scan/health").then(function (r) {{
      if (!r.ok) throw new Error("health");
      return fetch("/api/scan/upload?s=" + encodeURIComponent(SESSION), {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ image_b64: b64, filename: filename }})
      }});
    }}).then(function (r) {{
      return r.json().then(function (body) {{ return {{ r: r, body: body }}; }});
    }}).then(function (res) {{
      if (res.r.ok && res.body.ok) {{
        show("ok", "Sent — check the desktop app.");
      }} else {{
        sendBtn.disabled = false;
        show("err", (res.body && res.body.error) || "Upload failed.");
      }}
    }}).catch(function () {{
      sendBtn.disabled = false;
      fallback();
    }});
  }});

  // Reachability check on load so the user sees the fallback early.
  fetch("/api/scan/health").catch(function () {{ fallback(); }});
}})();
</script>
</body>
</html>"""


def _expired_page_html():
    """HTML shown when a scan session id is unknown or expired."""
    return """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>dubIS — Session expired</title>
<style>body{font-family:-apple-system,system-ui,sans-serif;margin:0;padding:32px;
line-height:1.5}h1{font-size:1.3rem}</style></head>
<body><h1>Scan session expired</h1>
<p>This scan link is no longer valid. Start a new scan from the desktop app to
get a fresh QR code.</p></body></html>"""


class PnPHandler(BaseHTTPRequestHandler):
    """HTTP request handler for PnP consumption events."""

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
        # The JSON envelope + base64 inflate the raw image by ~4/3 plus framing;
        # SCAN_MAX_IMAGE_BYTES * 2 is a generous bound for that overhead.
        if length > SCAN_MAX_IMAGE_BYTES * 2:
            # Don't read the (claimed) huge body — that's the whole point of the
            # early reject. Close the connection instead of trying to drain it.
            self.close_connection = True
            self._send_json(413, {"ok": False, "error": "Image too large"})
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

        image_b64 = body.get("image_b64") or ""
        filename = (body.get("filename") or "").strip()
        if not image_b64:
            self._send_json(400, {"ok": False, "error": "image_b64 is required"})
            return

        ext = os.path.splitext(filename)[1].lower()
        if ext not in SCAN_IMAGE_EXTS:
            self._send_json(400, {
                "ok": False,
                "error": f"Unsupported file type: {ext or '(none)'}",
            })
            return

        # Reject oversized payloads. base64 inflates by ~4/3; checking the
        # encoded length first avoids decoding a huge blob.
        if len(image_b64) * 3 // 4 > SCAN_MAX_IMAGE_BYTES:
            self._send_json(413, {"ok": False, "error": "Image too large"})
            return

        # Decode the base64 ourselves so malformed input is a client error (400)
        # rather than surfacing as an internal "OCR failed" 500 when the OCR path
        # re-decodes. The decoded bytes are used only for the size check here;
        # the original image_b64 string is still passed to OCR (re-decodes) and
        # to the _scanReceived UI push.
        try:
            decoded = base64.b64decode(image_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            self._send_json(400, {"ok": False, "error": f"Invalid base64 image data: {exc}"})
            return
        if len(decoded) > SCAN_MAX_IMAGE_BYTES:
            self._send_json(413, {"ok": False, "error": "Image too large"})
            return

        api = self.server.api
        window = self.server.window
        template = session["template"]

        try:
            line_items = api.parse_source_file_b64(image_b64, filename, template)
        except Exception as exc:
            logger.error("Scan OCR failed: %s", exc)
            self._send_json(500, {"ok": False, "error": f"OCR failed: {exc}"})
            return

        payload = {
            "line_items": line_items,
            "image_b64": image_b64,
            "filename": filename,
            "template": template,
        }
        try:
            window.evaluate_js("window._scanReceived(" + json.dumps(payload) + ")")
        except Exception as exc:
            logger.warning("Scan UI push failed (window may be closed): %s", exc)

        logger.info("Scan upload OCR'd %d line item(s) (template=%s)",
                    len(line_items), template)
        self._send_json(200, {"ok": True, "count": len(line_items)})

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


class _FastHTTPServer(HTTPServer):
    """HTTPServer that skips the slow FQDN reverse-DNS lookup in server_bind()."""

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

    thread = threading.Thread(target=server.serve_forever, name="pnp-server", daemon=True)
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
