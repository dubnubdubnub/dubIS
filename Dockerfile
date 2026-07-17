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
FROM python:3.12-slim

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

# Proves the container's dependency set actually satisfies the server's import
# graph at build time, not just "pip install didn't error" — inventory_api.py
# pulls in every domain/api_*.py facade; server/__main__.py's _build_api() wraps
# it directly, so if this import fails the container would fail at startup too.
RUN python -c "import server, inventory_api"

VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/v1/health', timeout=3).status == 200 else 1)"

CMD ["python", "-m", "server", "--data-dir", "/data", "--host", "0.0.0.0", "--port", "8080", "--static-dir", "/app"]
