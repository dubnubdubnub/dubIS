# dubis-server — headless /v1 API + static frontend, for remote/tailnet deployment.
#
# Desktop-only features (DigiKey WebView2/CDP scraping, OS file dialogs, OCR via
# tesseract) are NOT wired up in this image. Their API methods already fail with
# typed errors when the underlying dependency is unavailable — see
# docs/plans/2026-07-16-phase1c-remote-deploy-design.md §4 and "Risks". We do NOT
# install tesseract/opencv system libs here to make them work; pymupdf, pdfplumber,
# Pillow, and opencv-python-headless all ship manylinux wheels so `pip install`
# succeeds without extra apt packages — only `python -c "import server, inventory_api"`
# at the end of this build proves the runtime import graph is actually satisfied.
# python:3.12-slim, resolved 2026-07-17 via:
#   docker pull python:3.12-slim && docker inspect --format='{{index .RepoDigests 0}}' python:3.12-slim
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

WORKDIR /app

# Install Python deps first (separate layer from source) so `docker build` cache
# hits on source-only edits and skips the (slow) dependency resolve/download.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Backend: top-level *.py modules + domain/ + server/.
# Frontend (dogfooded — the app is an HTTP client of its own /v1 API, see
# CLAUDE.md's Architecture section): index.html, js/, css/.
COPY *.py .
COPY domain/ domain/
COPY server/ server/
# InventoryApi.__init__ unconditionally imports domain.api_mirror, which
# imports mirror_install.base/.tailscale — plain stdlib + subprocess, no
# desktop-only deps, so it imports cleanly on Linux even though the mirror's
# actual install/uninstall actions are desktop-only and unused in-container.
# Found the hard way: omitting this directory makes `import inventory_api`
# fail with ModuleNotFoundError before the server ever starts.
COPY mirror_install/ mirror_install/
COPY index.html .
COPY js/ js/
COPY css/ css/

# Static config baked into the image, NOT user data: inventory_api.py's
# _load_constants() reads data/constants.json at *import* time from a path
# relative to this module's own directory — unaffected by --data-dir, which
# only repoints the CSV/cache/prefs paths (see server/__main__.py's
# _build_api()). Without this copy the `import server, inventory_api` sanity
# check below fails even though /data (the actual runtime volume) is empty.
COPY data/constants.json data/constants.json

# Static UI assets baked into the image, NOT user data: the frontend requests
# these over HTTP from --static-dir /app (index.html's rel=icon and distributor
# filter buttons, js/inventory/inv-html-builders.js's part-ID row icons), so
# they must live at /app/data/ — the runtime volume mounts at /data, a
# different path, and cannot serve them. Without this copy every icon 404s in
# remote mode. data/dubIS.ico is omitted on purpose: it is only app.pyw's
# native window icon, never fetched over HTTP.
COPY data/dubIS.png data/digikey-icon.png data/lcsc-icon.ico data/mouser-icon.svg data/pololu-icon.svg data/

# Proves the container's dependency set actually satisfies the server's import
# graph at build time, not just "pip install didn't error" — inventory_api.py
# pulls in every domain/api_*.py facade; server/__main__.py's _build_api() wraps
# it directly, so if this import fails the container would fail at startup too.
RUN python -c "import server, inventory_api"

# Run as a fixed non-root uid — never as root in the container. The app
# writes CSVs/cache.db/.v1_port/.dubis_lock under /data at runtime (a VOLUME
# here; a PVC in k8s), so whatever owns/can-write /data at runtime must match
# this uid. NOTE for Task 5 (k8s Deployment): set
#   securityContext: { runAsUser: 10001, fsGroup: 10001 }
# on the pod so the PVC-backed /data mount is group-writable by this uid.
RUN useradd -m -u 10001 appuser
USER appuser

VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/v1/health', timeout=3).status == 200 else 1)"

CMD ["python", "-m", "server", "--data-dir", "/data", "--host", "0.0.0.0", "--port", "8080", "--static-dir", "/app"]
