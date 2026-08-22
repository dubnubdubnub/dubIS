"""Preferences facade — load/save preferences.

preferences.json is deliberately free-form: the JS store owns the shape, loads
the whole object, mutates one key, and posts the whole object back. So almost
nothing is validated here. The exception is the *reader* keys below, which have
a closed vocabulary and a consequence (downloading multi-GB weights, or dialling
a remote GPU) that a typo must not reach.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Mapping

import csv_io

logger = logging.getLogger(__name__)

# ── Reader preferences (docs/plans/2026-08-21-cross-platform-reader-design.md) ──
#
# Where the picture/PDF reader's VLM runs, and how it is reached:
#
#   off     nowhere         image/PDF import degrades to the tesseract/flat path
#   local   this machine    bundled-on-demand llama.cpp on loopback
#   remote  a fleet node    fleet discovery, or the explicit `reader_url`
#   auto    prefer local    probe local first, else fall back to the fleet
#
# `off` is the default, and a clean install must keep it: `local` downloads
# multi-GB model weights, which may only ever happen because someone clicked
# Install — never because the app started for the first time. The mode moves off
# `off` after a successful local install, not before.
READER_MODES: tuple[str, ...] = ("off", "local", "remote", "auto")
READER_MODE_DEFAULT = "off"

# `reader_url` is the escape hatch from fleet discovery: an explicit endpoint for
# `remote` mode. Empty is not "unset by accident" — it is the instruction to ask
# the fleet registry for a vision-capable node instead (see fleet_client.py).
READER_URL_DEFAULT = ""

_READER_URL_RE = re.compile(r"^https?://[^\s/?#]+", re.IGNORECASE)


class ReaderPreferenceError(ValueError):
    """A reader preference was written with a value outside its vocabulary.

    Subclasses ValueError so server/errors.py maps it to HTTP 400 (a client
    mistake) with no new entry in that table, while still being catchable as its
    own type by a caller that wants to distinguish it.
    """


def normalize_reader_mode(raw: Any) -> str:
    """Validate a written `reader_mode`, returning the canonical spelling.

    Empty/None is absence, not a typo (a hand-edited file, or a field the UI
    cleared), and reads back as the default. Anything else outside the
    vocabulary raises: coercing `"locl"` to `"off"` would leave the reader
    silently disabled with a preferences file that looks deliberately set.
    """
    if raw is None:
        return READER_MODE_DEFAULT
    if not isinstance(raw, str):
        raise ReaderPreferenceError(
            f"reader_mode must be a string, got {type(raw).__name__} ({raw!r}); "
            f"expected one of {', '.join(READER_MODES)}"
        )
    mode = raw.strip().lower()
    if not mode:
        return READER_MODE_DEFAULT
    if mode not in READER_MODES:
        raise ReaderPreferenceError(
            f"reader_mode {raw!r} is not a known mode; "
            f"expected one of {', '.join(READER_MODES)}"
        )
    return mode


def normalize_reader_url(raw: Any) -> str:
    """Validate a written `reader_url`, returning it without trailing slashes.

    Empty means "discover a node through the fleet registry" and is always
    allowed. A non-empty value must carry an http(s) scheme AND a host: without
    a scheme it would be resolved against whatever origin dialled it (pointing
    the reader at ourselves), and `http://` alone names no node at all. Trailing
    slashes are stripped so the stored value can be compared and joined without
    every call site re-normalizing it.

    Reachability is deliberately NOT checked: an endpoint that is merely powered
    off must stay typable.
    """
    if raw is None:
        return READER_URL_DEFAULT
    if not isinstance(raw, str):
        raise ReaderPreferenceError(
            f"reader_url must be a string, got {type(raw).__name__} ({raw!r})"
        )
    text = raw.strip()
    if not text:
        return READER_URL_DEFAULT
    if not _READER_URL_RE.match(text):
        raise ReaderPreferenceError(
            f"reader_url {raw!r} must be an http(s) URL with a host "
            "(e.g. http://y740.ts.net:8080), or empty to discover a fleet node"
        )
    return text.rstrip("/")


def resolve_reader_mode(preferences: Mapping[str, Any] | None) -> str:
    """Read `reader_mode` out of a loaded preferences mapping.

    The read path is forgiving where the write path is strict: preferences.json
    is user-editable, so a value we would have refused to write can still be
    found there, and it must degrade to `off` rather than crash an import. Same
    split as `remote_mode.resolve_remote_base_url`, which tolerates a non-string
    `server_url`.
    """
    if not preferences:
        return READER_MODE_DEFAULT
    try:
        return normalize_reader_mode(preferences.get("reader_mode"))
    except ReaderPreferenceError as exc:
        logger.warning("Ignoring unusable reader_mode in preferences: %s", exc)
        return READER_MODE_DEFAULT


def resolve_reader_url(preferences: Mapping[str, Any] | None) -> str:
    """Read `reader_url` out of a loaded preferences mapping.

    Returns "" for absent/unusable, which `remote`/`auto` mode reads as "ask the
    fleet registry" — the same answer as a deliberately blank field, and the
    right one either way.
    """
    if not preferences:
        return READER_URL_DEFAULT
    try:
        return normalize_reader_url(preferences.get("reader_url"))
    except ReaderPreferenceError as exc:
        logger.warning("Ignoring unusable reader_url in preferences: %s", exc)
        return READER_URL_DEFAULT


def validate_reader_preferences(prefs: dict[str, Any]) -> dict[str, Any]:
    """Return `prefs` with its reader keys normalized, raising on a bad value.

    Only keys that are actually PRESENT are touched. Injecting the defaults here
    would write `reader_mode` into every preferences.json the first time any
    unrelated preference is saved, and — because the JS store posts the whole
    in-memory object — would make the default indistinguishable from a choice.
    """
    if not isinstance(prefs, dict):
        return prefs
    normalized = prefs
    for key, normalize in (
        ("reader_mode", normalize_reader_mode),
        ("reader_url", normalize_reader_url),
    ):
        if key not in prefs:
            continue
        value = normalize(prefs[key])
        if value != prefs[key]:
            if normalized is prefs:
                normalized = dict(prefs)
            normalized[key] = value
    return normalized


class PreferencesFacade:
    def __init__(self, api) -> None:
        self._api = api

    def load_preferences(self) -> dict[str, Any]:
        """Read preferences.json and return its contents (empty dict if missing/corrupt).

        Returned verbatim, reader keys included: defaults live in
        `resolve_reader_mode`/`resolve_reader_url`, not here. See
        `validate_reader_preferences` for why nothing is injected.
        """
        try:
            if os.path.exists(self._api.prefs_json):
                with open(self._api.prefs_json, encoding="utf-8") as f:
                    data = json.load(f)
                # Migrate saved distributor_filter sets: "other" → "direct"
                if isinstance(data, dict) and isinstance(data.get("distributor_filter"), list):
                    data["distributor_filter"] = [
                        "direct" if d == "other" else d for d in data["distributor_filter"]
                    ]
                return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load preferences: %s", exc)
        return {}

    def save_preferences(self, prefs_json: str | dict[str, Any]) -> None:
        """Write preferences JSON string to disk.

        Reader keys are validated first, so the raise happens BEFORE the write:
        a rejected mode leaves the previous file (and a first-ever save leaves no
        file), rather than persisting a value nothing can act on.
        """
        prefs = self._api._ensure_parsed(prefs_json)
        prefs = validate_reader_preferences(prefs)
        csv_io.atomic_write_text(
            self._api.prefs_json, json.dumps(prefs, indent=2), encoding="utf-8",
        )
