# dubIS.spec — PyInstaller build spec (cross-platform)
import sys
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# pywebview pulls in platform-specific backends
hiddenimports = collect_submodules('webview')

a = Analysis(
    ['app.pyw'],
    pathex=[],
    binaries=[],
    # Every source path here must be TRACKED IN GIT, not merely present on the
    # machine that runs the build: PyInstaller aborts outright on a `datas`
    # entry whose source is missing, so anything gitignored builds fine for its
    # author and fails on every clean checkout. `data/preferences.json` was
    # exactly that (see tests/python/test_pyinstaller_spec.py, which now fails
    # instead of the build). It is not shipped: it is runtime state the app
    # writes, `data/*.json` in .gitignore, and deliberately deleted by
    # scripts/ci-scrub-workspace.sh. The defaults live in code — a missing file
    # makes InventoryApi.load_preferences() return {}, which every reader
    # already handles — so a seeded copy would only be a second set of defaults
    # free to drift from the real ones.
    #
    # This list is hand-written, which is a standing hazard: an asset added to
    # the repo is absent from the bundle BY DEFAULT and nothing about running
    # the app from source notices, because from source every path resolves to
    # the repo itself. Eight tracked assets went missing that way, two of them
    # fatal. data/constants.json was the worse one: inventory_api.py reads it
    # at *import* time, so the built app died with FileNotFoundError before it
    # imported webview — and had it survived that, js/constants.js throws on a
    # 404 of the same file and kills the frontend before it renders. The other
    # is splash.html, which is the window's first paint.
    # tests/python/test_pyinstaller_spec.py now derives what the app demands at
    # runtime — frontend static URLs, module-relative Python reads, and every
    # tracked file under data/ — and fails when an entry here does not cover
    # it. Add the asset AND let the guard tell you if you missed one.
    #
    # Destinations mirror the repo layout on purpose: the /v1 server is started
    # with `static_dir=APP_DIR` and Python modules resolve `data/...` against
    # their own directory, so both consumers expect the bundle root to look
    # exactly like a checkout. The guard asserts that too.
    datas=[
        # Frontend documents. splash.html is what pywebview shows first; it
        # polls /v1/health and then navigates itself to index.html.
        ('index.html', '.'),
        ('splash.html', '.'),
        ('css', 'css'),
        ('js', 'js'),
        # Static config the frontend fetches and the backend imports.
        # js/constants.js throws on a 404 here, and inventory_api.py reads the
        # same file at import time relative to its own module directory.
        ('data/constants.json', 'data'),
        # Window/taskbar icons (read off disk by app.pyw) and the browser-tab
        # icon (fetched over HTTP by index.html).
        ('data/dubIS.png', 'data'),
        ('data/dubIS.ico', 'data'),
        # Distributor icons — <img src="data/..."> in index.html and in the
        # row HTML js/inventory/inv-html-builders.js builds.
        ('data/digikey-icon.png', 'data'),
        ('data/lcsc-icon.ico', 'data'),
        ('data/mouser-icon.svg', 'data'),
        ('data/pololu-icon.svg', 'data'),
        # Read server-side: the Tier-1 family table (server/routes/openpnp.py,
        # relative to its own module dir) and the OpenPnP part-id mapping
        # (pnp_part_map.py, relative to the data dir). Both answer a missing
        # file with a silent empty default, so omitting them degrades OpenPnP
        # quietly instead of erroring.
        ('data/openpnp_families.json', 'data'),
        ('data/pnp_part_map.json', 'data'),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# --- Platform-specific icon ---
icon_file = 'data/dubIS.ico' if sys.platform == 'win32' else 'data/dubIS.png'

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='dubIS',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,    # GUI app, no terminal window
    icon=icon_file,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='dubIS',
)

# macOS app bundle
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='dubIS.app',
        icon='data/dubIS.png',
        bundle_identifier='com.gehub.dubis',
        info_plist={
            'CFBundleShortVersionString': '1.0.0',
            'NSHighResolutionCapable': True,
        },
    )
