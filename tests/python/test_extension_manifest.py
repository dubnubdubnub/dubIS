"""Guard: the JLC bridge extension's permission set, asserted exactly.

The realistic failure mode for a cookie-reading extension is not v1 — it is a
routine change months later that adds `<all_urls>`, `tabs` or `scripting`
because something was easier that way, and turns a narrowly-scoped tool into a
confused deputy anything on the internet can aim. Reachability is the danger,
not the cookies (rule 9 of docs/plans/2026-09-20-extension-credential-capture.md).

So this test pins the sets rather than checking "does not contain <all_urls>":
a widening must fail CI and be an explicit, reviewed edit to the expectations
below, never drift.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "extension" / "jlc-bridge" / "manifest.json"

# The ID Chrome derives from the `key` below. Named in three other places —
# extension/jlc-bridge/README.md, the plan doc, and the server's CORS allowlist
# — so it is pinned here and *derived* rather than trusted (see the section at
# the bottom of this file).
EXPECTED_EXTENSION_ID = "fboadceadnhfhdkdmfjlhbicocbhbbpc"

# Exactly these. Chrome scopes `cookies` by host_permissions, so the pair below
# IS the security boundary: cookie reads anywhere but jlcpcb.com are refused by
# the browser itself.
EXPECTED_PERMISSIONS = {"cookies", "storage"}
EXPECTED_HOST_PERMISSIONS = {"*://*.jlcpcb.com/*"}

# Keys whose mere presence changes what can reach the extension:
#   externally_connectable -> a web page could message the service worker
#   content_scripts        -> extension code would run inside pages
# Phase 1 is push-only and gesture-initiated; neither may appear.
FORBIDDEN_KEYS = ("externally_connectable", "content_scripts")

# Ask-later keys are the same widening by another route: the user grants them at
# runtime and the manifest diff never shows a new capability.
FORBIDDEN_OPTIONAL_KEYS = ("optional_permissions", "optional_host_permissions")


def _manifest() -> dict:
    assert MANIFEST_PATH.is_file(), f"missing extension manifest: {MANIFEST_PATH}"
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_manifest_is_mv3():
    assert _manifest().get("manifest_version") == 3


def test_permissions_match_exactly():
    permissions = _manifest().get("permissions", [])
    assert isinstance(permissions, list)
    assert len(permissions) == len(set(permissions)), f"duplicate permissions: {permissions}"
    assert set(permissions) == EXPECTED_PERMISSIONS, (
        "the extension's `permissions` changed.\n"
        f"  expected: {sorted(EXPECTED_PERMISSIONS)}\n"
        f"  found:    {sorted(permissions)}\n"
        "Widening this is a deliberate, reviewed decision — see rule 1 of "
        "docs/plans/2026-09-20-extension-credential-capture.md — so update the "
        "expectation here in the same PR that argues for it."
    )


def test_host_permissions_match_exactly():
    hosts = _manifest().get("host_permissions", [])
    assert isinstance(hosts, list)
    assert len(hosts) == len(set(hosts)), f"duplicate host_permissions: {hosts}"
    assert set(hosts) == EXPECTED_HOST_PERMISSIONS, (
        "the extension's `host_permissions` changed.\n"
        f"  expected: {sorted(EXPECTED_HOST_PERMISSIONS)}\n"
        f"  found:    {sorted(hosts)}\n"
        "Enumerated hosts only, never <all_urls>. Adding digikey.com is phase 2 "
        "and updates this expectation explicitly."
    )


def test_manifest_pins_the_extension_id():
    """`key` fixes the extension ID across the move to the Web Store.

    Unpacked dev-mode loads derive the ID from the folder path; a Web Store
    upload derives it from the signing key. Shipping the public half here makes
    both produce the same ID, so anything pinning it survives the migration.
    Drop this field and the ID silently changes — which is exactly the kind of
    "it still loads fine locally" breakage nothing else catches.

    The matching PRIVATE key is deliberately not in the repo; see
    extension/jlc-bridge/README.md for where it lives.
    """
    key = _manifest().get("key")
    assert isinstance(key, str) and key.strip(), (
        "manifest.json lost its `key` field, so the extension ID is no longer "
        "stable across an unpacked load and a Web Store upload. Restore the "
        "base64 public key — see extension/jlc-bridge/README.md."
    )
    # A base64 DER SPKI for RSA-2048 is ~392 chars; anything much shorter is a
    # placeholder, not a key.
    assert len(key) > 100, f"`key` looks too short to be a public key ({len(key)} chars)"
    assert "PRIVATE" not in key.upper(), "that is a PRIVATE key — only the public half belongs here"


def test_forbidden_keys_are_absent():
    manifest = _manifest()
    present = [key for key in FORBIDDEN_KEYS if key in manifest]
    assert not present, (
        f"manifest declares {present}, which makes the extension reachable from "
        "web pages. Phase 1 is push-only and gesture-initiated (rule 2)."
    )


def test_optional_permission_keys_are_absent():
    manifest = _manifest()
    present = [key for key in FORBIDDEN_OPTIONAL_KEYS if key in manifest]
    assert not present, (
        f"manifest declares {present} — a runtime-granted widening that this "
        "guard's exact sets would otherwise never see."
    )


def test_no_all_urls_anywhere_in_the_manifest():
    """Belt and braces: catches <all_urls> in a key this guard does not name."""
    text = MANIFEST_PATH.read_text(encoding="utf-8")
    assert "<all_urls>" not in text, "the manifest mentions <all_urls> somewhere"


# ── The pinned ID, derived rather than retyped ───────────────────────────────
#
# `manifest.json`'s `key` and `server.routes.distributors.BRIDGE_EXTENSION_ID`
# are two copies of one fact, written in different notations, in different
# languages, in different halves of the repo. The server answers the
# extension's CORS preflight for exactly that one origin, so if the two ever
# disagree the handshake breaks *silently*: Chrome reports "Failed to fetch"
# and dubIS never sees a request to log. That is a day of debugging (it already
# was one, on 2026-09-20), so it gets a test rather than a comment.
#
# Chrome's derivation, which is what the tests below re-run: sha256 the DER
# SubjectPublicKeyInfo the `key` field base64-encodes, keep the first 16 bytes,
# hex them, then map each hex digit 0-f onto a-p — the "mpdecimal" alphabet
# that makes an extension ID all letters.


def _derive_extension_id(key_b64: str) -> str:
    digest = hashlib.sha256(base64.b64decode(key_b64)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(char, 16)) for char in digest)


def test_the_manifest_key_derives_the_documented_extension_id():
    """The ID in the README and the plan is the one this `key` actually makes.

    Guards the direction nothing else can: swap `key` for a different keypair's
    public half and every *other* manifest test still passes, while the ID
    every document names becomes fiction.
    """
    derived = _derive_extension_id(_manifest()["key"])
    assert derived == EXPECTED_EXTENSION_ID, (
        "manifest.json's `key` no longer derives the pinned extension ID.\n"
        f"  documented: {EXPECTED_EXTENSION_ID}\n"
        f"  derived:    {derived}\n"
        "Either the key was replaced (then the ID changed for every installed "
        "copy, and extension/jlc-bridge/README.md plus the server allowlist "
        "need updating together), or this expectation drifted."
    )


def test_bridge_origin_matches_the_manifest_key():
    """The server's allowlist and the manifest's key are the same extension.

    `server/routes/distributors.py` answers the credential-intake preflight for
    `BRIDGE_EXTENSION_ORIGIN` and 403s every other origin — that scoping is the
    whole reason the extension did not need a loopback host permission. It is
    only sound while the ID it names is the ID Chrome assigns this manifest.
    """
    from server.routes import distributors

    derived = _derive_extension_id(_manifest()["key"])
    assert distributors.BRIDGE_EXTENSION_ID == derived, (
        "the server allowlists an extension ID that manifest.json's `key` does "
        "not produce, so the JLC bridge's POST will fail its CORS preflight "
        "with no server-side trace.\n"
        f"  server BRIDGE_EXTENSION_ID: {distributors.BRIDGE_EXTENSION_ID}\n"
        f"  derived from the manifest:  {derived}\n"
        "See docs/plans/2026-09-20-extension-credential-capture.md, "
        '"Resolved: the scoped CORS preflight".'
    )
    assert distributors.BRIDGE_EXTENSION_ORIGIN == f"chrome-extension://{derived}"
