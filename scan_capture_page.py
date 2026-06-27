"""Capture-page HTML generators for the phone-facing scan flow.

Extracted from pnp_server.py — pure presentation, no server state.
"""

import html
import json
import logging

logger = logging.getLogger(__name__)


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
  #thumbs { margin-bottom: 16px; }
  .po-group { border: 1px dashed #cbd5e1; border-radius: 10px; padding: 8px;
    margin-bottom: 10px; }
  .po-group-label { font-size: 0.8rem; color: #666; margin-bottom: 6px; }
  .po-group-thumbs { display: flex; flex-wrap: wrap; gap: 8px; }
  .thumb { position: relative; width: 84px; height: 84px; border-radius: 8px;
    overflow: hidden; border: 1px solid #cbd5e1; cursor: pointer; }
  .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .thumb.selected { outline: 3px solid #2563eb; outline-offset: -3px; }
  .thumb-check { position: absolute; top: 2px; left: 2px; width: 22px;
    height: 22px; border-radius: 50%; background: #2563eb; color: #fff;
    font-size: 13px; line-height: 22px; text-align: center; display: none; }
  .thumb.selected .thumb-check { display: block; }
  .thumb-remove { position: absolute; top: 2px; right: 2px; width: 24px;
    height: 24px; min-width: 0; padding: 0; margin: 0; border-radius: 50%;
    background: rgba(0, 0, 0, 0.6); color: #fff; font-size: 16px;
    line-height: 24px; border: none; }
  #group-controls { display: none; gap: 8px; margin-bottom: 16px; }
  #group-controls button { margin-bottom: 0; padding: 12px; font-size: 0.95rem; }
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
<div id="group-controls">
  <button id="group-sel" class="ghost" type="button">Group selected</button>
  <button id="ungroup-sel" class="ghost" type="button">Ungroup</button>
</div>

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
  var groupControls = document.getElementById("group-controls");
  var groupSelBtn = document.getElementById("group-sel");
  var ungroupSelBtn = document.getElementById("ungroup-sel");
  // Each photo is its own PO by default; the user groups same-order pages.
  var photos = [];  // [{ id, file, dataUrl, name, group }]
  var selected = {};  // photo id -> true (tap-select for grouping)
  var nextId = 0, nextGroup = 0;

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

  // Distinct PO groups as arrays of photo refs, ordered by first appearance.
  function orderedGroups() {
    var byGroup = {};
    var order = [];
    photos.forEach(function (p) {
      if (!(p.group in byGroup)) { byGroup[p.group] = []; order.push(p.group); }
      byGroup[p.group].push(p);
    });
    return order.map(function (g) { return byGroup[g]; });
  }

  function selectedIds() {
    return photos.filter(function (p) { return selected[p.id]; });
  }

  function toggleSelect(id) {
    if (selected[id]) delete selected[id]; else selected[id] = true;
    afterPhotosChanged();
  }

  function removePhoto(id) {
    photos = photos.filter(function (p) { return p.id !== id; });
    delete selected[id];
    afterPhotosChanged();
  }

  function groupSelected() {
    var sel = selectedIds();
    if (sel.length < 2) return;
    var gid = nextGroup++;
    sel.forEach(function (p) { p.group = gid; });
    selected = {};
    afterPhotosChanged();
  }

  function ungroupSelected() {
    var sel = selectedIds();
    if (!sel.length) return;
    sel.forEach(function (p) { p.group = nextGroup++; });  // each → its own PO
    selected = {};
    afterPhotosChanged();
  }

  // Render captured pages grouped into PO sections; tap a thumb to select it.
  function renderGroups() {
    thumbs.innerHTML = "";
    orderedGroups().forEach(function (grp, k) {
      var section = document.createElement("div");
      section.className = "po-group";
      var label = document.createElement("div");
      label.className = "po-group-label";
      label.textContent = "PO " + (k + 1);
      section.appendChild(label);
      var row = document.createElement("div");
      row.className = "po-group-thumbs";
      grp.forEach(function (p) {
        var cell = document.createElement("div");
        cell.className = "thumb" + (selected[p.id] ? " selected" : "");
        var img = document.createElement("img");
        img.src = p.dataUrl;
        img.alt = p.name;
        var check = document.createElement("span");
        check.className = "thumb-check";
        check.textContent = "\\u2713";
        var rm = document.createElement("button");
        rm.type = "button";
        rm.className = "thumb-remove";
        rm.setAttribute("aria-label", "Remove photo");
        rm.textContent = "\\u00d7";
        rm.addEventListener("click", function (e) { e.stopPropagation(); removePhoto(p.id); });
        cell.addEventListener("click", function () { toggleSelect(p.id); });
        cell.appendChild(img);
        cell.appendChild(check);
        cell.appendChild(rm);
        row.appendChild(cell);
      });
      section.appendChild(row);
      thumbs.appendChild(section);
    });
  }

  // Refresh all UI that depends on the current photo set.
  function afterPhotosChanged() {
    renderGroups();
    var orders = orderedGroups().length;
    sendBtn.disabled = photos.length === 0;
    sendBtn.textContent = photos.length
      ? "Send " + photos.length + " photo" + (photos.length === 1 ? "" : "s")
        + (orders > 1 ? " \\u00b7 " + orders + " orders" : "") + " to desktop"
      : "Send to desktop";
    savePhotosBtn.style.display = canSharePhotos() ? "block" : "none";
    groupControls.style.display = photos.length >= 2 ? "flex" : "none";
    var selCount = selectedIds().length;
    groupSelBtn.disabled = selCount < 2;
    ungroupSelBtn.disabled = selCount < 1;
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
        photos.push({ id: nextId++, file: f, dataUrl: reader.result,
          name: f.name || ("scan" + (photos.length + 1) + ".jpg"), group: nextGroup++ });
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

  groupSelBtn.addEventListener("click", groupSelected);
  ungroupSelBtn.addEventListener("click", ungroupSelected);

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
    // Map the on-screen PO grouping to photo indices for the backend.
    var groups = orderedGroups().map(function (grp) {
      return grp.map(function (p) { return photos.indexOf(p); });
    });
    var payload = JSON.stringify({ images: images, groups: groups });

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
