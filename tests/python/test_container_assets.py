"""Guard: every data/ asset the frontend loads as a static URL is in the image.

The desktop app and dubis-server disagree about what "data/" means. On the
desktop the static root *is* the repo, so `<img src="data/lcsc-icon.ico">`
resolves to the same file the backend reads. In the container the static root
is /app and the data dir is /data (a PVC), so that same URL resolves to
/app/data/lcsc-icon.ico — a file that exists only because the Dockerfile
copies it. A frontend asset added under data/ and not copied therefore looks
perfect locally and 404s on every remote client, which is exactly how the
distributor icons and the browser-tab icon went missing.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# `src="data/foo.png"`, `href='data/foo.png'`, `fetch('data/foo.json')`. The
# trailing group requires a real filename, so the runtime-built prefix in
# js/ui-helpers.js's vendorIconSrc (`"data/" + p`) is correctly not matched —
# those paths point into the user's data dir and are served as data: URIs
# instead (see vendorIconFor).
_REF_RE = re.compile(r"""["'(]data/([A-Za-z0-9_.\-]+\.[A-Za-z0-9]+)""")


def _frontend_data_refs() -> dict[str, list[str]]:
    """Map each referenced data/ filename -> the files referencing it."""
    sources = [REPO_ROOT / "index.html", REPO_ROOT / "splash.html"]
    sources += sorted(REPO_ROOT.glob("js/**/*.js"))
    sources += sorted(REPO_ROOT.glob("css/**/*.css"))

    refs: dict[str, list[str]] = {}
    for path in sources:
        if not path.is_file():
            continue
        for name in _REF_RE.findall(path.read_text(encoding="utf-8")):
            refs.setdefault(name, []).append(str(path.relative_to(REPO_ROOT)))
    return refs


def _dockerfile_data_copies() -> set[str]:
    """Filenames under data/ that the Dockerfile copies into the image."""
    text = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    # Fold backslash line continuations so a multi-line COPY parses as one.
    text = re.sub(r"\\\s*\n\s*", " ", text)

    copied: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line.upper().startswith("COPY "):
            continue
        tokens = line.split()[1:]
        if len(tokens) < 2:
            continue
        for src in tokens[:-1]:  # last token is the destination
            if src.startswith("data/"):
                copied.add(src[len("data/"):])
    return copied


def _dockerignore_allowed_data_files() -> set[str]:
    """Filenames under data/ that survive .dockerignore's `data/**` exclusion."""
    text = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    allowed: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("!data/"):
            allowed.add(line[len("!data/"):])
    return allowed


def test_every_copied_data_file_survives_dockerignore():
    """The COPY list and the .dockerignore exemption list must agree.

    .dockerignore excludes `data/**`, which removes the file from the build
    *context* — so a COPY of an unexempted file does not quietly produce an
    image without it, it fails the build with "not found". Nothing short of an
    actual `docker build` catches this, which is exactly why it needs a guard:
    a copy of the repo's own filesystem (the obvious way to sanity-check the
    static layout locally) never goes through the build context at all.
    """
    copied = _dockerfile_data_copies()
    allowed = _dockerignore_allowed_data_files()

    excluded = copied - allowed
    assert not excluded, (
        "the Dockerfile COPYs these files out of data/, but .dockerignore's "
        "`data/**` rule keeps them out of the build context, so `docker build` "
        "fails with \"not found\":\n"
        + "\n".join(f"  data/{name}" for name in sorted(excluded))
        + "\nAdd a `!data/<name>` line to .dockerignore for each."
    )


def test_frontend_data_assets_are_copied_into_the_image():
    refs = _frontend_data_refs()
    copied = _dockerfile_data_copies()

    missing = {name: users for name, users in refs.items() if name not in copied}
    assert not missing, (
        "these data/ files are loaded as static URLs by the frontend but are "
        "not COPYed into the container image, so they 404 on every remote "
        "client while working fine on the desktop app:\n"
        + "\n".join(f"  data/{name}  <- {', '.join(users)}" for name, users in sorted(missing.items()))
        + "\nAdd them to the data/ COPY in the Dockerfile."
    )


def test_guard_actually_sees_the_known_assets():
    """The guard is only worth having if its two halves find real entries."""
    refs = _frontend_data_refs()
    assert "lcsc-icon.ico" in refs and "dubIS.png" in refs
    assert "constants.json" in _dockerfile_data_copies()


def test_openpnp_family_table_is_copied_into_the_image():
    """Not a frontend asset, so the scan above cannot see it.

    server/routes/openpnp.py reads data/openpnp_families.json relative to its
    own module directory (/app/data in the container, never --data-dir), and
    _load_families() answers a missing file with `{}` — every part silently
    degrades to tier:"unmapped" rather than erroring, so nothing surfaces the
    omission at runtime.
    """
    assert "openpnp_families.json" in _dockerfile_data_copies()
