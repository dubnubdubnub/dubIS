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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

logger = logging.getLogger(__name__)

PNP_PORT = 7890

# Scan session time-to-live (seconds). A QR code / capture page is only useful
# for a short window; expire sessions so stale ids can't be replayed.
SCAN_SESSION_TTL = 15 * 60

# Max accepted decoded image size for an upload (bytes). Phone photos are a few
# MB; 15 MB gives generous headroom while rejecting abuse. Enforced PER IMAGE.
SCAN_MAX_IMAGE_BYTES = 15 * 1024 * 1024

# Max number of images in a single multi-photo upload (one PO can span several
# printed pages). Bounds memory + OCR work per request.
SCAN_MAX_IMAGES = 12

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
    """Build the mobile capture page. All CSS/JS inline; no external assets.

    The two dynamic values (template label, session id) are substituted via
    sentinel replacement rather than an f-string so the page's CSS/JS braces
    don't need doubling — keeping the embedded JavaScript readable.
    """
    return (
        _CAPTURE_PAGE_TEMPLATE
        .replace("@@TEMPLATE@@", html.escape(template))
        .replace("@@SESSION_JSON@@", json.dumps(session_id))
    )


_CAPTURE_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>dubIS — Scan a PO</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, system-ui, sans-serif; margin: 0;
    padding: 24px; line-height: 1.5; }
  h1 { font-size: 1.3rem; margin: 0 0 4px; }
  .tmpl { color: #666; font-size: 0.9rem; margin-bottom: 24px; }
  label.btn, button { display: block; width: 100%; padding: 18px;
    font-size: 1.1rem; text-align: center; border-radius: 12px; border: none;
    margin-bottom: 16px; cursor: pointer; }
  label.btn { background: #2563eb; color: #fff; }
  label.btn.secondary, button.ghost { background: transparent; color: #2563eb;
    border: 1px solid #2563eb; }
  button.send { background: #16a34a; color: #fff; }
  button:disabled { opacity: 0.5; }
  input[type=file] { display: none; }
  #preview-wrap { position: relative; display: none; max-width: 100%;
    margin-bottom: 16px; }
  #preview { display: block; width: 100%; border-radius: 12px; }
  #ocr-overlay-layer { position: absolute; inset: 0; pointer-events: none; }
  .ocr-box { position: absolute; border: 1.5px solid rgba(37, 99, 235, 0.9);
    background: rgba(37, 99, 235, 0.12); border-radius: 2px; }
  #thumbs { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }
  .thumb { position: relative; width: 84px; height: 84px; border-radius: 8px;
    overflow: hidden; border: 1px solid #cbd5e1; }
  .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .thumb-remove { position: absolute; top: 2px; right: 2px; width: 24px;
    height: 24px; min-width: 0; padding: 0; margin: 0; border-radius: 50%;
    background: rgba(0, 0, 0, 0.6); color: #fff; font-size: 16px;
    line-height: 24px; border: none; }
  .msg { padding: 14px; border-radius: 10px; margin-bottom: 16px;
    display: none; }
  .msg.ok { background: #dcfce7; color: #166534; display: block; }
  .msg.err { background: #fee2e2; color: #991b1b; display: block; }
  .hint { font-size: 0.85rem; color: #666; }
  #save-photos { display: none; }
  #progress-wrap { display: none; margin-bottom: 16px; }
  #progress-track { background: #e5e7eb; border-radius: 8px; height: 14px;
    overflow: hidden; }
  #progress-bar { background: #2563eb; height: 100%; width: 0%;
    transition: width 0.1s linear; }
  #progress-text { font-size: 0.85rem; color: #666; margin-top: 6px;
    text-align: center; }
</style>
</head>
<body>
<h1>Scan a Purchase Order</h1>
<div class="tmpl">Template: <strong>@@TEMPLATE@@</strong></div>

<div id="msg" class="msg"></div>

<label class="btn" for="file">Take a photo</label>
<input id="file" type="file" accept="image/*" capture="environment">

<label class="btn secondary" for="file-library">Upload existing photo(s)</label>
<input id="file-library" type="file" accept="image/*" multiple>

<div id="thumbs"></div>

<div id="preview-wrap">
  <img id="preview" alt="preview">
  <div id="ocr-overlay-layer"></div>
</div>
<button id="save-photos" class="ghost" type="button">📥 Save to Photos</button>

<div id="progress-wrap">
  <div id="progress-track"><div id="progress-bar"></div></div>
  <div id="progress-text"></div>
</div>

<button id="send" class="send" disabled>Send to desktop</button>

<p class="hint">Snap each page of the purchase order — tap <em>Take a photo</em>
again to add more — or upload existing photos, then tap <em>Send</em>.</p>

<script>
(function () {
  var SESSION = @@SESSION_JSON@@;
  var cameraInput = document.getElementById("file");
  var libraryInput = document.getElementById("file-library");
  var preview = document.getElementById("preview");
  var previewWrap = document.getElementById("preview-wrap");
  var overlayLayer = document.getElementById("ocr-overlay-layer");
  var thumbs = document.getElementById("thumbs");
  var sendBtn = document.getElementById("send");
  var msg = document.getElementById("msg");
  var savePhotosBtn = document.getElementById("save-photos");
  var progressWrap = document.getElementById("progress-wrap");
  var progressBar = document.getElementById("progress-bar");
  var progressText = document.getElementById("progress-text");
  var photos = [];  // [{ file, dataUrl, name }] — one per captured/picked page

  function show(kind, text) {
    msg.className = "msg " + kind;
    msg.textContent = text;
  }
  function fallback() {
    show("err", "Couldn't reach the app. Save the photo and use "
      + "'Choose a file' on the desktop instead.");
  }
  function fmtBytes(n) {
    if (n >= 1048576) return (n / 1048576).toFixed(1) + " MB";
    if (n >= 1024) return Math.round(n / 1024) + " KB";
    return n + " B";
  }
  function fmtSpeed(bps) {
    if (!isFinite(bps) || bps <= 0) return "";
    if (bps >= 1048576) return (bps / 1048576).toFixed(1) + " MB/s";
    return Math.round(bps / 1024) + " KB/s";
  }
  // iOS can't auto-save a web-captured photo to the camera roll; the share
  // sheet (navigator.share with files) is the only route, and the user taps
  // "Save Image" there. Feature-detect so we only show the button when usable.
  function canSharePhotos() {
    try {
      if (!navigator.canShare || !photos.length) return false;
      return navigator.canShare({ files: photos.map(function (p) { return p.file; }) });
    } catch (e) {
      return false;
    }
  }

  // Render the thumbnail strip of captured pages, each with a remove button.
  function renderThumbs() {
    thumbs.innerHTML = "";
    photos.forEach(function (p, idx) {
      var cell = document.createElement("div");
      cell.className = "thumb";
      var img = document.createElement("img");
      img.src = p.dataUrl;
      img.alt = p.name;
      var rm = document.createElement("button");
      rm.type = "button";
      rm.className = "thumb-remove";
      rm.setAttribute("aria-label", "Remove photo");
      rm.textContent = "\\u00d7";
      rm.addEventListener("click", function () {
        photos.splice(idx, 1);
        afterPhotosChanged();
      });
      cell.appendChild(img);
      cell.appendChild(rm);
      thumbs.appendChild(cell);
    });
  }

  // Refresh all UI that depends on the current photo set.
  function afterPhotosChanged() {
    renderThumbs();
    sendBtn.disabled = photos.length === 0;
    sendBtn.textContent = photos.length
      ? "Send " + photos.length + " photo" + (photos.length === 1 ? "" : "s") + " to desktop"
      : "Send to desktop";
    savePhotosBtn.style.display = canSharePhotos() ? "block" : "none";
    previewWrap.style.display = "none";
    overlayLayer.innerHTML = "";
    progressWrap.style.display = "none";
    msg.className = "msg";
  }

  // Draw the OCR-detected token boxes over the captured photo. Coordinates are
  // in the OCR image's pixel space; the preview is that same image, so we place
  // each box as a percentage of the page width/height.
  function renderOcrOverlay(page) {
    overlayLayer.innerHTML = "";
    if (!page || !page.width || !page.height) return;
    var toks = (page.words && page.words.length) ? page.words : (page.lines || []);
    for (var i = 0; i < toks.length; i++) {
      var t = toks[i];
      var box = document.createElement("div");
      box.className = "ocr-box";
      box.style.left = (t.x / page.width * 100) + "%";
      box.style.top = (t.y / page.height * 100) + "%";
      box.style.width = (t.w / page.width * 100) + "%";
      box.style.height = (t.h / page.height * 100) + "%";
      overlayLayer.appendChild(box);
    }
  }

  // Append every chosen file (camera capture is one at a time; the library
  // picker can return several) to the photo set, reading each as a data URL.
  function addFiles(fileList) {
    var files = Array.prototype.slice.call(fileList || []);
    if (!files.length) return;
    var remaining = files.length;
    files.forEach(function (f) {
      var reader = new FileReader();
      reader.onload = function () {
        photos.push({ file: f, dataUrl: reader.result,
          name: f.name || ("scan" + (photos.length + 1) + ".jpg") });
        remaining -= 1;
        if (remaining === 0) afterPhotosChanged();
      };
      reader.onerror = function () {
        remaining -= 1;
        show("err", "Could not read a photo.");
        if (remaining === 0) afterPhotosChanged();
      };
      reader.readAsDataURL(f);
    });
  }

  // Clear input.value after reading so picking the SAME file again (or another
  // camera shot) still fires 'change'.
  cameraInput.addEventListener("change", function () {
    addFiles(cameraInput.files);
    cameraInput.value = "";
  });
  libraryInput.addEventListener("change", function () {
    addFiles(libraryInput.files);
    libraryInput.value = "";
  });

  savePhotosBtn.addEventListener("click", function () {
    if (!photos.length || !navigator.share) return;
    navigator.share({ files: photos.map(function (p) { return p.file; }),
      title: "Purchase order scan" })
      .catch(function () { /* user cancelled or share unavailable — non-fatal */ });
  });

  function uploadWithProgress(payload) {
    var xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/scan/upload?s=" + encodeURIComponent(SESSION));
    xhr.setRequestHeader("Content-Type", "application/json");
    var startTime = Date.now();
    var lastTime = startTime, lastLoaded = 0;
    xhr.upload.onprogress = function (e) {
      if (!e.lengthComputable) return;
      var pct = Math.round((e.loaded / e.total) * 100);
      progressBar.style.width = pct + "%";
      var now = Date.now();
      var dt = (now - lastTime) / 1000;
      var speed = dt > 0 ? (e.loaded - lastLoaded) / dt : 0;
      if (dt > 0) { lastTime = now; lastLoaded = e.loaded; }
      var speedStr = fmtSpeed(speed);
      progressText.textContent = pct + "% \\u00b7 " + fmtBytes(e.loaded)
        + " / " + fmtBytes(e.total) + (speedStr ? " \\u00b7 " + speedStr : "");
    };
    xhr.onload = function () {
      progressBar.style.width = "100%";
      var body = null;
      try { body = JSON.parse(xhr.responseText); } catch (e) { body = null; }
      if (xhr.status >= 200 && xhr.status < 300 && body && body.ok) {
        var secs = (Date.now() - startTime) / 1000;
        progressText.textContent = "Uploaded" + (secs > 0 ? " in " + secs.toFixed(1) + "s" : "");
        // Overlay the first page's detected tokens on its photo and report a
        // verdict so the user can confirm (or retake) right here on the phone.
        var pages = body.pages || [];
        if (pages.length && photos.length) {
          preview.src = photos[0].dataUrl;
          previewWrap.style.display = "inline-block";
          renderOcrOverlay(pages[0]);
        }
        var count = body.count || 0;
        var orders = body.orders || 1;
        if (count > 0) {
          var itemStr = count + " item" + (count === 1 ? "" : "s");
          var orderStr = orders > 1 ? (" in " + orders + " orders") : "";
          show("ok", "Found " + itemStr + orderStr + " - review on the desktop app.");
        } else {
          show("err", "Couldn't read any parts. Retake with more light, fill the "
            + "frame, and hold the page flat.");
        }
      } else {
        sendBtn.disabled = false;
        progressWrap.style.display = "none";
        show("err", (body && body.error) || "Upload failed.");
      }
    };
    xhr.onerror = function () {
      sendBtn.disabled = false;
      progressWrap.style.display = "none";
      fallback();
    };
    xhr.send(payload);
  }

  sendBtn.addEventListener("click", function () {
    if (!photos.length) return;
    sendBtn.disabled = true;
    show("ok", "Sending…");
    var images = photos.map(function (p) {
      var comma = p.dataUrl.indexOf(",");
      return { image_b64: comma >= 0 ? p.dataUrl.slice(comma + 1) : p.dataUrl,
        filename: p.name };
    });
    var payload = JSON.stringify({ images: images });

    progressWrap.style.display = "block";
    progressBar.style.width = "0%";
    progressText.textContent = "Starting\\u2026";

    // Reachability check first so an unreachable app fails fast with the hint.
    fetch("/api/scan/health").then(function (r) {
      if (!r.ok) throw new Error("health");
      uploadWithProgress(payload);
    }).catch(function () {
      sendBtn.disabled = false;
      progressWrap.style.display = "none";
      fallback();
    });
  });

  // Reachability check on load so the user sees the fallback early.
  fetch("/api/scan/health").catch(function () { fallback(); });
})();
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


def _save_scan_image(base_dir, image_bytes, ext):
    """Persist an uploaded scan image to ``<base_dir>/scans`` and return its path.

    Called the moment a phone upload arrives (before OCR) so the original photo
    is always kept on the desktop, even if OCR fails or the user never finishes
    the import. Filenames are timestamped with a short random suffix so two
    uploads in the same second can't collide.
    """
    scans_dir = os.path.join(base_dir, "scans")
    os.makedirs(scans_dir, exist_ok=True)
    name = f"scan_{time.strftime('%Y%m%d-%H%M%S')}_{secrets.token_hex(3)}{ext}"
    path = os.path.join(scans_dir, name)
    with open(path, "wb") as f:
        f.write(image_bytes)
    return path


class _ScanUploadError(Exception):
    """Client-error during scan-upload validation, carrying the HTTP status."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def _validate_scan_image(entry):
    """Validate one upload image dict; return {image_b64, filename, ext, decoded}.

    Raises _ScanUploadError(status, message) on any client-side problem so the
    handler can surface the same 400/413 responses it always has.
    """
    image_b64 = (entry.get("image_b64") if isinstance(entry, dict) else "") or ""
    filename = ((entry.get("filename") if isinstance(entry, dict) else "") or "").strip()
    if not image_b64:
        raise _ScanUploadError(400, "image_b64 is required")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in SCAN_IMAGE_EXTS:
        raise _ScanUploadError(400, f"Unsupported file type: {ext or '(none)'}")
    # base64 inflates by ~4/3; check the encoded length before decoding a blob.
    if len(image_b64) * 3 // 4 > SCAN_MAX_IMAGE_BYTES:
        raise _ScanUploadError(413, "Image too large")
    try:
        decoded = base64.b64decode(image_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _ScanUploadError(400, f"Invalid base64 image data: {exc}") from exc
    if len(decoded) > SCAN_MAX_IMAGE_BYTES:
        raise _ScanUploadError(413, "Image too large")
    return {"image_b64": image_b64, "filename": filename, "ext": ext, "decoded": decoded}


def _normalize_groups(raw, n):
    """Coerce a client-supplied photo grouping into a clean partition of range(n).

    Each inner list is one PO (photo indices). Out-of-range, duplicate, and
    non-integer indices are dropped; any photo not covered becomes its own group;
    empty groups are removed. Falls back to one-group-per-photo when *raw* isn't a
    usable list. Groups are ordered by their first photo index for stable output.
    """
    default = [[i] for i in range(n)]
    if not isinstance(raw, list):
        return default
    seen = set()
    groups = []
    for grp in raw:
        if not isinstance(grp, list):
            continue
        members = []
        for idx in grp:
            if (isinstance(idx, int) and not isinstance(idx, bool)
                    and 0 <= idx < n and idx not in seen):
                seen.add(idx)
                members.append(idx)
        if members:
            groups.append(sorted(members))
    for i in range(n):  # uncovered photos each become their own PO
        if i not in seen:
            groups.append([i])
    groups.sort(key=lambda g: g[0])
    return groups or default


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
