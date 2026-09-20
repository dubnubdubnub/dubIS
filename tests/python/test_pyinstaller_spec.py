"""Guard: ``dubIS.spec`` ships what the app needs, and only what a clone has.

Two opposite failure modes, both silent, both invisible when you run from
source (from source every path resolves to the repo itself, so the bundle's
layout is never exercised):

1. A path in ``datas`` that a clean clone does not have — the build aborts.
   That is the half this file originally guarded; see below.
2. A path the app needs at runtime that is *not* in ``datas`` — the build is
   green and the app is broken. The spec enumerates by hand, so every asset
   added to the repo is missing from the bundle by default. Eight went that
   way at once. Two were fatal: ``data/constants.json`` (``inventory_api.py``
   reads it at *import* time, so the built app raised FileNotFoundError before
   it even imported webview — and ``js/constants.js`` throws on a 404 of the
   same file, so the frontend would have died too) and ``splash.html`` (the
   window's first paint). The rest were the four distributor icons and the
   two OpenPnP tables. The second half of this file closes that: it derives what
   the app demands — from the frontend's static URLs, from module-relative
   Python reads, and from the tracked contents of ``data/`` — and fails when
   ``datas`` does not cover it.

This is the desktop twin of ``test_container_assets.py``, which guards the
same property for the container image, and it is written in the same shape.

--- half one: every path the spec feeds PyInstaller must survive a clean clone

PyInstaller resolves ``Analysis(datas=...)`` before it does anything else and
aborts the whole build on the first source path it cannot find::

    ERROR: Unable to find '<repo>/data/preferences.json' when adding binary
    and data files.

PyInstaller resolves ``Analysis(datas=...)`` before it does anything else and
aborts the whole build on the first source path it cannot find::

    ERROR: Unable to find '<repo>/data/preferences.json' when adding binary
    and data files.

That makes a gitignored source path a build-breaking bug that is *invisible to
whoever introduced it*: the file exists on their machine because the app wrote
it at runtime. ``data/preferences.json`` sat in ``datas`` exactly that way —
ignored by .gitignore's ``data/*.json`` rule, absent from every fresh clone,
and no CI job builds the spec, so nothing noticed.

Plain existence is therefore the wrong assertion — it would have passed on the
machine that shipped the bug. These tests demand the stronger property the
build actually needs: the path is **tracked in git**, so it is in every clone.

The spec is read with ``ast`` rather than executed, because executing it
requires PyInstaller, which is not (and need not be) a dev dependency.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC = REPO_ROOT / "dubIS.spec"


def _analysis_call() -> ast.Call:
    tree = ast.parse(SPEC.read_text(encoding="utf-8"), filename=str(SPEC))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Analysis"
        ):
            return node
    raise AssertionError(f"no Analysis(...) call found in {SPEC}")


def _literal_list(node: ast.expr, what: str) -> list:
    try:
        return ast.literal_eval(node)
    except ValueError as exc:  # pragma: no cover - only on a spec rewrite
        raise AssertionError(
            f"{SPEC.name}'s {what} is no longer a literal list, so this guard "
            f"can no longer check it. Keep it literal, or teach the guard the "
            f"new shape — do not leave the paths unchecked: {exc}",
        ) from exc


def _spec_scripts() -> list[str]:
    call = _analysis_call()
    assert call.args, "Analysis(...) has no scripts argument"
    return _literal_list(call.args[0], "scripts list")


def _spec_datas() -> list[tuple[str, str]]:
    call = _analysis_call()
    for kw in call.keywords:
        if kw.arg == "datas":
            return _literal_list(kw.value, "datas")
    raise AssertionError("Analysis(...) has no datas= keyword")


def _tracked(rel: str) -> list[str]:
    """Paths git tracks under ``rel`` (the path itself, or a directory's files)."""
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "--", rel],
        capture_output=True, text=True, check=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def _spec_paths() -> list[str]:
    return [*_spec_scripts(), *(src for src, _dest in _spec_datas())]


def test_spec_declares_the_paths_this_guard_expects() -> None:
    """A rewrite that empties datas must not silently empty the guard too."""
    paths = _spec_paths()
    assert len(paths) >= 6, f"suspiciously few paths parsed out of {SPEC.name}: {paths}"
    assert "app.pyw" in paths


@pytest.mark.parametrize("rel", _spec_paths())
def test_spec_path_exists(rel: str) -> None:
    assert (REPO_ROOT / rel).exists(), (
        f"{SPEC.name} feeds PyInstaller '{rel}', which does not exist — "
        f"the build aborts before Analysis with "
        f"\"Unable to find ... when adding binary and data files\"."
    )


@pytest.mark.parametrize("rel", _spec_paths())
def test_spec_path_is_tracked_in_git(rel: str) -> None:
    """Existing locally is not enough — it must exist in a fresh clone."""
    assert _tracked(rel), (
        f"{SPEC.name} feeds PyInstaller '{rel}', which git does not track "
        f"(gitignored, or never added). It may exist on your machine — the app "
        f"writes some of these at runtime — but a clean checkout will not have "
        f"it and the build will abort. Either commit it, or drop it from the "
        f"spec's datas."
    )


def test_runtime_written_preferences_are_not_shipped() -> None:
    """The specific regression: data/preferences.json must stay out of datas.

    It is user state the app writes, its defaults live in code (a missing file
    makes ``load_preferences()`` return ``{}``), and shipping a seed copy would
    be a second set of defaults free to drift from the real ones.
    """
    sources = [src for src, _dest in _spec_datas()]
    assert "data/preferences.json" not in sources


# ---------------------------------------------------------------------------
# Half two: every asset the app demands at runtime must be IN the bundle.
#
# Three independent derivations, because no single one sees everything:
#   (A) what the frontend loads as a static URL,
#   (B) what Python resolves against its own module directory / APP_DIR,
#   (C) every tracked file under data/ — the default-flip that catches what
#       (A) and (B) structurally cannot see.
# ---------------------------------------------------------------------------


def _bundled() -> dict[str, str]:
    """Repo-relative source path -> bundle destination, for everything shipped.

    ``scripts`` counts as bundled: ``app.pyw`` is compiled into the archive,
    so a reference to it is satisfied without a ``datas`` entry.
    """
    shipped = {src: dest for src, dest in _spec_datas()}
    for script in _spec_scripts():
        shipped.setdefault(script, ".")
    return shipped


def _covers(rel: str) -> bool:
    """Is ``rel`` shipped, either directly or inside a bundled directory?"""
    shipped = _bundled()
    if rel in shipped:
        return True
    return any(
        rel.startswith(src.rstrip("/") + "/")
        for src in shipped
        if (REPO_ROOT / src).is_dir()
    )


# `src="data/foo.png"`, `href='data/foo.png'`, `fetch('data/foo.json')`. Same
# regex as test_container_assets.py, and for the same reason: the trailing
# group requires a real filename, so js/ui-helpers.js's runtime-built
# `"data/" + p` prefix is correctly not matched — those point into the *user's*
# data dir and travel as data: URIs, not as bundled files.
_DATA_REF_RE = re.compile(r"""["'(]data/([A-Za-z0-9_.\-]+\.[A-Za-z0-9]+)""")

# A relative src=/href= in an HTML document: no scheme, not root-relative, not
# a fragment. `data:` URIs are excluded by the scheme test, not by accident.
_HTML_REF_RE = re.compile(r"""(?:src|href)\s*=\s*["']([^"'#][^"']*)["']""")

_HTML_ENTRY_DOCS = ("index.html", "splash.html")


def _frontend_static_refs() -> dict[str, list[str]]:
    """(A) Repo-relative paths the frontend loads over HTTP -> who loads them.

    The desktop app starts the /v1 server with ``static_dir=APP_DIR``, so every
    relative URL in the shipped pages resolves inside the bundle. A referenced
    file that is not bundled 404s.
    """
    refs: dict[str, list[str]] = {}

    for name in _HTML_ENTRY_DOCS:
        path = REPO_ROOT / name
        if not path.is_file():
            continue
        for url in _HTML_REF_RE.findall(path.read_text(encoding="utf-8")):
            if "://" in url or url.startswith(("/", "data:", "mailto:")):
                continue
            rel = url.split("?", 1)[0].split("#", 1)[0]
            if rel and (REPO_ROOT / rel).is_file():
                refs.setdefault(rel, []).append(name)

    scanned = [REPO_ROOT / n for n in _HTML_ENTRY_DOCS]
    scanned += sorted(REPO_ROOT.glob("js/**/*.js"))
    scanned += sorted(REPO_ROOT.glob("css/**/*.css"))
    for path in scanned:
        if not path.is_file():
            continue
        for name in _DATA_REF_RE.findall(path.read_text(encoding="utf-8")):
            rel = f"data/{name}"
            if (REPO_ROOT / rel).is_file():
                refs.setdefault(rel, []).append(str(path.relative_to(REPO_ROOT)))

    return refs


# Names whose value is a bundle-rooted directory. `__file__` covers every
# module-relative read (inventory_api.py's data/constants.json,
# server/routes/openpnp.py's family table); APP_DIR is app.pyw's own alias for
# the same root.
_BUNDLE_ROOT_NAMES = {"__file__", "APP_DIR"}

# The Python that gets frozen. Not scripts/ or tools/ (build-time and agent
# tooling, never bundled) and not tests/.
_FROZEN_PY_GLOBS = ("*.py", "*.pyw", "server/**/*.py", "domain/**/*.py",
                    "mirror_install/**/*.py")


def _python_bundle_refs() -> dict[str, list[str]]:
    """(B) Files Python resolves against the bundle root -> who resolves them.

    Finds ``os.path.join(<something rooted at __file__/APP_DIR>, "a", "b")``
    where every following segment is a string literal, and keeps the results
    that name a file this repo actually has. A non-literal segment ends the
    path (so ``os.path.join(APP_DIR, "data", SENTINEL)`` is skipped, honestly
    — see the limitations note on the tests below), and a result that is a
    directory or does not exist is dropped: those are runtime-created state
    (``data/webview2``), not assets to ship.
    """
    refs: dict[str, list[str]] = {}
    files: list[Path] = []
    for pattern in _FROZEN_PY_GLOBS:
        files += sorted(REPO_ROOT.glob(pattern))

    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "join"):
                continue
            if not node.args:
                continue
            rooted = any(
                isinstance(sub, ast.Name) and sub.id in _BUNDLE_ROOT_NAMES
                for sub in ast.walk(node.args[0])
            )
            if not rooted:
                continue
            segments: list[str] = []
            for arg in node.args[1:]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    segments.append(arg.value)
                else:
                    break
            if not segments:
                continue
            rel = "/".join(segments)
            if (REPO_ROOT / rel).is_file():
                who = f"{path.relative_to(REPO_ROOT)}:{node.lineno}"
                refs.setdefault(rel, []).append(who)

    return refs


# (C) Tracked files under data/ that are deliberately NOT shipped. Every entry
# needs a reason, because the whole point of the default-flip is that adding a
# tracked data/ file forces a decision instead of quietly shipping nothing.
_NOT_BUNDLED: dict[str, str] = {
    "data/.gitkeep": "placeholder that keeps the empty dir in git; ships nothing",
    "data/README.txt": "documents the data dir for a human reading the repo",
    "data/Cart_Mar25_0912PM.xls": "sample import file; nothing in the app reads it",
}


def _tracked_data_files() -> list[str]:
    return [p for p in _tracked("data") if not p.endswith("/")]


def test_frontend_static_assets_are_bundled() -> None:
    """A referenced file that is not in the bundle 404s at runtime.

    ``data/constants.json`` is the fatal one: js/constants.js does
    ``if (!resp.ok) throw`` at module scope, so the whole frontend dies on a
    missing file rather than degrading.
    """
    missing = {
        rel: who for rel, who in _frontend_static_refs().items() if not _covers(rel)
    }
    assert not missing, (
        "the frontend loads these over HTTP, but dubIS.spec does not bundle "
        "them, so they 404 in the built app while working fine from source:\n"
        + "\n".join(f"  {rel}  <- {', '.join(sorted(set(who)))}"
                    for rel, who in sorted(missing.items()))
        + "\nAdd each to Analysis(datas=...) with its repo directory as the dest."
    )


def test_python_resolved_assets_are_bundled() -> None:
    """``splash.html`` is the fatal one here: no splash, no first paint."""
    missing = {
        rel: who for rel, who in _python_bundle_refs().items() if not _covers(rel)
    }
    assert not missing, (
        "these files are resolved against the bundle root by Python but are "
        "not bundled, so the built app gets FileNotFoundError or a silent "
        "empty default:\n"
        + "\n".join(f"  {rel}  <- {', '.join(sorted(set(who)))}"
                    for rel, who in sorted(missing.items()))
        + "\nAdd each to Analysis(datas=...) with its repo directory as the dest."
    )


@pytest.mark.parametrize("rel", _tracked_data_files())
def test_tracked_data_file_is_bundled_or_explicitly_excused(rel: str) -> None:
    """The default flips: a tracked data/ file ships unless excused here.

    The two scans above only see what they can *statically* trace. They cannot
    see ``pnp_part_map.py``'s ``os.path.join(base_dir, "pnp_part_map.json")``,
    where the root arrives as a function parameter — which is exactly how that
    file went missing. This test needs no trace: it asks whether every tracked
    file in data/ was deliberately handled, so a new asset is a decision rather
    than an omission.
    """
    if rel in _NOT_BUNDLED:
        assert not _covers(rel), (
            f"{rel} is listed in _NOT_BUNDLED but dubIS.spec bundles it. "
            f"Drop it from _NOT_BUNDLED, or from datas."
        )
        return
    assert _covers(rel), (
        f"{rel} is tracked in data/ but dubIS.spec does not bundle it. On the "
        f"desktop the static root IS the repo, so this resolves from source "
        f"and is simply absent from the built app.\n"
        f"Either add ('{rel}', '{Path(rel).parent.as_posix()}') to "
        f"Analysis(datas=...), or add it to _NOT_BUNDLED in this file with a "
        f"reason it is deliberately not shipped."
    )


@pytest.mark.parametrize("src,dest", _spec_datas())
def test_bundle_layout_mirrors_the_repo(src: str, dest: str) -> None:
    """Both consumers assume the bundle root looks like a checkout.

    ``app.pyw`` starts the /v1 server with ``static_dir=APP_DIR`` (so the
    frontend's relative URLs resolve) and Python modules read ``data/...``
    relative to their own directory. A right file at the wrong destination is
    the same outage as a missing one, and ``datas`` lets you write either.
    """
    expected = src.rsplit("/", 1)[0] if "/" in src else "."
    if (REPO_ROOT / src).is_dir():
        expected = src
    got = dest.replace("\\", "/").rstrip("/") or "."
    assert got == expected, (
        f"dubIS.spec bundles '{src}' to '{dest}', but the app looks for it at "
        f"'{expected}/'. The bundle root must mirror the repo layout."
    )


def test_frozen_app_dir_is_the_directory_datas_land_in() -> None:
    """``APP_DIR`` must be ``sys._MEIPASS``, not ``dirname(sys.executable)``.

    Those are different directories in every build this spec produces:
    PyInstaller 6 puts a onedir payload in ``_internal/`` beside the launcher,
    and a macOS .app keeps the launcher in ``Contents/MacOS`` and the payload
    in ``Contents/Frameworks``. Resolving bundled assets against the executable
    therefore points at a directory holding nothing but the launcher — the
    bundle is complete and the app still cannot find splash.html.

    Static, because importing app.pyw needs a GUI stack and a webview import.
    """
    source = (REPO_ROOT / "app.pyw").read_text(encoding="utf-8")
    match = re.search(
        r"if getattr\(sys, ['\"]frozen['\"], False\):\s*\n\s*APP_DIR = (.+)", source
    )
    assert match, "app.pyw no longer has a recognizable frozen APP_DIR branch"
    assert "_MEIPASS" in match.group(1), (
        "app.pyw resolves APP_DIR from "
        f"{match.group(1).strip()!r} when frozen. Bundled assets live under "
        "sys._MEIPASS; the executable's directory holds only the launcher."
    )


def test_the_runtime_scans_actually_find_the_known_assets() -> None:
    """A derivation that silently matches nothing would pass forever."""
    frontend = _frontend_static_refs()
    assert "data/constants.json" in frontend
    assert "data/lcsc-icon.ico" in frontend
    assert "js/app-init.js" in frontend

    python_refs = _python_bundle_refs()
    assert "splash.html" in python_refs
    assert "data/openpnp_families.json" in python_refs

    tracked = _tracked_data_files()
    assert "data/pnp_part_map.json" in tracked
    assert len(tracked) >= 8, f"suspiciously few tracked data/ files: {tracked}"
