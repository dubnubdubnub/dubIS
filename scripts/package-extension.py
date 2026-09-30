#!/usr/bin/env python3
"""Build the dubIS bridge extension into a Chrome Web Store upload zip.

    python scripts/package-extension.py                      # dist/dubis-bridge-<version>.zip
    python scripts/package-extension.py --first-upload       # strip `key` for a first Store upload
    python scripts/package-extension.py --first-upload --key-pem ~/secure/dubis-bridge.pem

What goes in: only the files the extension runs (the manifest, its scripts and
pages, the icons). The README, CHANGELOG and PRIVACY pages stay out; they are
for people reading the repo, not for the browser.

Before writing anything, the build checks that every file the manifest names
and every relative `import` in a script resolves to a file in the package. A
missing file is an extension that loads unpacked (where the whole folder is
present) and breaks from the Store. That is the failure this refuses to ship.

THE KEY, AND THE TWO WAYS TO KEEP THE EXTENSION ID
`manifest.json` carries the public half of the signing key, which pins the ID
the server's CORS allowlist trusts (`BRIDGE_EXTENSION_ID`). The Chrome Web
Store refuses a `key` field on an extension's *first* upload, so
`--first-upload` strips it. Then either:

1. **Keep today's ID.** Pass `--key-pem` with the matching private key and it
   is added to the zip as `key.pem`, which the Store uses as the extension's
   key. That zip then contains a secret: upload it and delete it.
2. **Take the Store's ID.** Upload without `--key-pem`, copy the public key
   from the Developer Dashboard's Package tab into `manifest.json`, and update
   `BRIDGE_EXTENSION_ID`. `tests/python/test_extension_manifest.py` fails
   until the two agree. This is the route Chrome documents.

Later uploads keep `key` in the manifest, so the default build leaves it in.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTENSION_DIR = REPO_ROOT / "extension" / "dubis-bridge"
DIST_DIR = REPO_ROOT / "dist"

# What the browser needs, by pattern. Anything else in the folder is left out.
RUNTIME_GLOBS = ("manifest.json", "*.js", "*.html", "*.css", "icons/*.png")

# A fixed timestamp so two builds of the same tree are byte-identical.
_ZIP_EPOCH = (2026, 1, 1, 0, 0, 0)

_IMPORT_RE = re.compile(r"""(?:^|\s)(?:import|export)\s[^;]*?from\s+["'](\.{1,2}/[^"']+)["']""", re.M)
_BARE_IMPORT_RE = re.compile(r"""^\s*import\s+["'](\.{1,2}/[^"']+)["']""", re.M)
_HTML_SRC_RE = re.compile(r"""<(?:script|link)[^>]+(?:src|href)=["']([^"':]+)["']""", re.I)


class PackageError(Exception):
    """The extension folder cannot be packaged as it stands."""


def runtime_files(ext_dir: Path = EXTENSION_DIR) -> list[Path]:
    files: set[Path] = set()
    for pattern in RUNTIME_GLOBS:
        files.update(p for p in ext_dir.glob(pattern) if p.is_file())
    return sorted(files)


def manifest_references(manifest: dict) -> set[str]:
    """Every file path the manifest itself names."""
    refs: set[str] = set()
    refs.update((manifest.get("icons") or {}).values())
    action = manifest.get("action") or {}
    if action.get("default_popup"):
        refs.add(action["default_popup"])
    icon = action.get("default_icon")
    if isinstance(icon, dict):
        refs.update(icon.values())
    elif isinstance(icon, str):
        refs.add(icon)
    background = manifest.get("background") or {}
    if background.get("service_worker"):
        refs.add(background["service_worker"])
    for key in ("options_page",):
        if manifest.get(key):
            refs.add(manifest[key])
    options_ui = manifest.get("options_ui") or {}
    if options_ui.get("page"):
        refs.add(options_ui["page"])
    return refs


def check(ext_dir: Path = EXTENSION_DIR) -> tuple[dict, list[Path]]:
    """Validate the folder; return the manifest and the files to package."""
    manifest_path = ext_dir / "manifest.json"
    if not manifest_path.is_file():
        raise PackageError(f"no manifest.json in {ext_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = runtime_files(ext_dir)
    rel = {p.relative_to(ext_dir).as_posix() for p in files}

    problems: list[str] = []
    for ref in sorted(manifest_references(manifest)):
        if ref not in rel:
            problems.append(f"manifest names {ref!r}, which is not in the package")
    for path in files:
        text = path.read_text(encoding="utf-8") if path.suffix in (".js", ".html") else ""
        found = set(_IMPORT_RE.findall(text)) | set(_BARE_IMPORT_RE.findall(text))
        if path.suffix == ".html":
            found |= {s for s in _HTML_SRC_RE.findall(text) if not s.startswith(("http", "//"))}
        for target in sorted(found):
            resolved = (path.parent / target).resolve()
            try:
                inside = resolved.relative_to(ext_dir.resolve()).as_posix()
            except ValueError:
                problems.append(f"{path.name} imports {target!r}, which is outside the extension")
                continue
            if inside not in rel:
                problems.append(f"{path.name} references {target!r}, which is not in the package")
    if problems:
        raise PackageError("cannot package the extension:\n  " + "\n  ".join(problems))
    return manifest, files


def build(
    out_dir: Path = DIST_DIR,
    *,
    first_upload: bool = False,
    key_pem: Path | None = None,
    ext_dir: Path = EXTENSION_DIR,
) -> Path:
    """Write the zip and return its path."""
    if key_pem is not None and not first_upload:
        raise PackageError("--key-pem only makes sense with --first-upload")
    manifest, files = check(ext_dir)
    if key_pem is not None:
        key_text = Path(key_pem).expanduser().read_text(encoding="utf-8")
        if "PRIVATE KEY" not in key_text:
            raise PackageError(f"{key_pem} does not look like a PEM private key")

    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = "-first-upload" if first_upload else ""
    out = out_dir / f"dubis-bridge-{manifest['version']}{suffix}.zip"
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            name = path.relative_to(ext_dir).as_posix()
            if name == "manifest.json" and first_upload:
                data = dict(manifest)
                data.pop("key", None)
                payload = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode()
            else:
                payload = path.read_bytes()
            zf.writestr(zipfile.ZipInfo(name, date_time=_ZIP_EPOCH), payload)
        if key_pem is not None:
            zf.writestr(zipfile.ZipInfo("key.pem", date_time=_ZIP_EPOCH), key_text.encode())
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--first-upload", action="store_true",
                        help="strip the manifest `key` (the Store refuses it on a first upload)")
    parser.add_argument("--key-pem", type=Path,
                        help="with --first-upload: add this private key as key.pem to keep the pinned ID")
    parser.add_argument("--out-dir", type=Path, default=DIST_DIR)
    parser.add_argument("--check", action="store_true", help="validate only; write nothing")
    args = parser.parse_args(argv)
    try:
        if args.check:
            manifest, files = check()
            print(f"dubis-bridge {manifest['version']}: {len(files)} files, all references resolve")
            return 0
        out = build(args.out_dir, first_upload=args.first_upload, key_pem=args.key_pem)
    except PackageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(out)
    if args.key_pem:
        print("warning: this zip contains the private signing key. Upload it, then delete it.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
