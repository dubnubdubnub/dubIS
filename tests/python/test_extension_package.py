"""The extension packager: what goes in the Store zip, and what must not."""

from __future__ import annotations

import importlib.util
import json
import shutil
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "package_extension", REPO_ROOT / "scripts" / "package-extension.py")
package_extension = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(package_extension)

EXT = package_extension.EXTENSION_DIR


def _names(zip_path: Path) -> set[str]:
    with zipfile.ZipFile(zip_path) as zf:
        return set(zf.namelist())


def _manifest(zip_path: Path) -> dict:
    with zipfile.ZipFile(zip_path) as zf:
        return json.loads(zf.read("manifest.json"))


def test_the_real_extension_packages_cleanly(tmp_path):
    out = package_extension.build(tmp_path)
    manifest = json.loads((EXT / "manifest.json").read_text())
    assert out.name == f"dubis-bridge-{manifest['version']}.zip"
    names = _names(out)
    assert "manifest.json" in names
    assert {"background.js", "popup.html", "options.html"} <= names
    assert {f"icons/icon-{s}.png" for s in (16, 32, 48, 128)} <= names


def test_docs_and_secrets_stay_out(tmp_path):
    names = _names(package_extension.build(tmp_path))
    assert not {n for n in names if n.endswith(".md")}, names
    assert "key.pem" not in names
    assert not any(n.endswith(".pem") or n.endswith(".crx") for n in names)


def test_default_build_keeps_the_pinned_key(tmp_path):
    # Every upload after the first carries `key`, so the ID stays pinned.
    assert _manifest(package_extension.build(tmp_path))["key"]


def test_first_upload_strips_the_key_and_nothing_else(tmp_path):
    shipped = _manifest(package_extension.build(tmp_path, first_upload=True))
    source = json.loads((EXT / "manifest.json").read_text())
    source.pop("key")
    assert shipped == source


def test_key_pem_goes_in_only_on_a_first_upload(tmp_path):
    pem = tmp_path / "k.pem"
    pem.write_text("-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n")
    out = package_extension.build(tmp_path / "d", first_upload=True, key_pem=pem)
    assert "key.pem" in _names(out)
    with pytest.raises(package_extension.PackageError, match="first-upload"):
        package_extension.build(tmp_path / "d2", key_pem=pem)


def test_a_key_read_from_the_keychain_goes_in_as_key_pem(tmp_path, monkeypatch):
    pem = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n"
    out = package_extension.build(tmp_path, first_upload=True, key_text=pem)
    with zipfile.ZipFile(out) as zf:
        assert zf.read("key.pem").decode() == pem


def test_keychain_lookup_failures_are_loud(monkeypatch):
    import subprocess

    def _missing(*a, **k):
        raise subprocess.CalledProcessError(44, a[0], stderr="The specified item could not be found.")

    monkeypatch.setattr(subprocess, "run", _missing)
    with pytest.raises(package_extension.PackageError, match="no Keychain item"):
        package_extension.keychain_pem()


def test_the_key_cannot_come_from_two_places(tmp_path):
    pem = tmp_path / "k.pem"
    pem.write_text("-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n")
    with pytest.raises(package_extension.PackageError, match="not both"):
        package_extension.build(tmp_path / "d", first_upload=True, key_pem=pem, key_text="x")


def test_a_non_key_file_is_refused_as_key_pem(tmp_path):
    bogus = tmp_path / "k.pem"
    bogus.write_text("not a key")
    with pytest.raises(package_extension.PackageError, match="PEM private key"):
        package_extension.build(tmp_path / "d", first_upload=True, key_pem=bogus)


def test_two_builds_are_byte_identical(tmp_path):
    a = package_extension.build(tmp_path / "a").read_bytes()
    b = package_extension.build(tmp_path / "b").read_bytes()
    assert a == b


def _copy_extension(tmp_path) -> Path:
    dest = tmp_path / "ext"
    shutil.copytree(EXT, dest)
    return dest


def test_a_missing_icon_is_refused(tmp_path):
    ext = _copy_extension(tmp_path)
    (ext / "icons" / "icon-48.png").unlink()
    with pytest.raises(package_extension.PackageError, match="icon-48.png"):
        package_extension.check(ext)


def test_a_broken_relative_import_is_refused(tmp_path):
    # The failure an unpacked load hides: the whole folder is present there.
    ext = _copy_extension(tmp_path)
    bg = ext / "background.js"
    bg.write_text('import { x } from "./nowhere.js";\n' + bg.read_text())
    with pytest.raises(package_extension.PackageError, match="nowhere.js"):
        package_extension.check(ext)


def test_the_cli_check_mode_passes_on_the_real_tree(capsys):
    assert package_extension.main(["--check"]) == 0
    assert "all references resolve" in capsys.readouterr().out
