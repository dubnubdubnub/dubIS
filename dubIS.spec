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
    datas=[
        ('index.html', '.'),
        ('css', 'css'),
        ('js', 'js'),
        ('data/dubIS.png', 'data'),
        ('data/dubIS.ico', 'data'),
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
