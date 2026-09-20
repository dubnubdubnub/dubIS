"""Per-source bearer tokens, kept OUT of `data/preferences.json`.

## Why a second file

A token is the credential that gets this hub past another dubIS server's
`DUBIS_AUTH_MODE=on`. `data/preferences.json` is the wrong home for one, and the
reason is not squeamishness about secrets in general — it is that preferences is
a file with *traffic*:

* `GET /v1/preferences` hands the whole file to the browser on every startup, so
  a token living there is readable from the page's own console and visible in
  the DevTools network pane of any window the user opens.
* `js/store.js` round-trips that whole object back through
  `PUT /v1/preferences`, through a `normalizeServers` that only knows
  `{id, name, url}` — so a token stored beside the roster was *erased* by the
  next save of any unrelated preference (a slider nudge would do it), leaving a
  server that silently 401s.
* It is a file people copy into backups, sync between machines and paste into
  issues.

Redacting on the way out would fix the first two, but redaction is a policy that
has to stay remembered at every new read site. A separate file is a *structural*
guarantee: there is no token in the object `/v1/preferences` serves, because
there is no token in the file it reads.

This also restores the decision `docs/plans/2026-07-16-phase1c-remote-deploy-design.md`
already made ("never in preferences.json … token file pattern matches
`data/mirror_token` precedent"), which the multi-server-hub work reversed
without a stated rationale.

## Shape

`<data_dir>/server_tokens.json`, an object keyed by **source id** — the same ids
`servers[]` uses, so a roster entry and its credential are joined by the one
field that does not change when the user renames or re-points a server:

    {"bench": "tok_abc…", "shop": "tok_def…"}

Ignored by `.gitignore`'s `data/*.json` (which re-includes only the three
tracked config files by name), and written `0600` so it is not world-readable on
a shared machine.

Never logged: every function here reports *whether* a token exists, never what
it is. `tests/python/server/test_token_store.py` pins that.
"""

from __future__ import annotations

import json
import logging
import os
import stat

logger = logging.getLogger(__name__)

TOKEN_FILENAME = "server_tokens.json"

# `data/preferences.json`'s legacy home for the same secret. Read once, for
# migration, then removed — see `migrate_legacy_tokens`.
LEGACY_ENTRY_KEY = "token"


def token_path(data_dir: str) -> str:
    """The token file beside the data dir's other state."""
    return os.path.join(data_dir, TOKEN_FILENAME)


def load_tokens(data_dir: str) -> dict[str, str]:
    """`{source_id: token}`; `{}` when the file is absent.

    A malformed file is a warning and an empty mapping, never a raise: it would
    otherwise take down every `/v1` request (the registry is rebuilt per
    request), and losing a credential should degrade into "that source needs its
    token re-entered", not into an unusable app. The warning names the path, not
    the contents.
    """
    path = token_path(data_dir)
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning(
            "token_store: %s is unreadable (%s) — treating every source as having no "
            "token. Re-enter any credential in Preferences > Server.",
            path, type(exc).__name__,
        )
        return {}
    if not isinstance(raw, dict):
        logger.warning("token_store: %s is not a JSON object — ignoring it", path)
        return {}
    out: dict[str, str] = {}
    for source_id, token in raw.items():
        if not isinstance(source_id, str) or not isinstance(token, str):
            logger.warning("token_store: ignoring a non-string entry in %s", path)
            continue
        source_id = source_id.strip()
        token = token.strip()
        if source_id and token:
            out[source_id] = token
    return out


def save_tokens(data_dir: str, tokens: dict[str, str]) -> None:
    """Replace the token file with *tokens*, dropping blanks.

    Written to a temp file in the same directory and renamed, so a crash
    mid-write cannot leave a half-written file that `load_tokens` would read as
    "no tokens at all" — i.e. as every remote source silently losing its
    credential.

    An empty mapping deletes the file rather than writing `{}`: nothing to
    protect means nothing on disk to leak.
    """
    kept = {
        str(k).strip(): str(v).strip()
        for k, v in (tokens or {}).items()
        if str(k or "").strip() and str(v or "").strip()
    }
    path = token_path(data_dir)
    if not kept:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return

    os.makedirs(data_dir, exist_ok=True)
    tmp = path + ".tmp"
    # 0600 at creation, not after: a chmod after the write leaves a window in
    # which the file exists world-readable.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(kept, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def migrate_legacy_tokens(prefs: dict, data_dir: str) -> bool:
    """Move any `servers[].token` out of *prefs* and into the token file.

    Mutates *prefs* in place (stripping the key) and returns True when it found
    something, so the caller knows preferences needs rewriting.

    This is the upgrade path for anyone who ran the multi-server-hub build that
    stored tokens in preferences.json. A token already in the token file wins —
    the file is the writer now, and a stale preferences copy must not resurrect
    a credential the user has since changed.
    """
    roster = prefs.get("servers")
    if not isinstance(roster, list):
        return False
    found: dict[str, str] = {}
    for entry in roster:
        if not isinstance(entry, dict):
            continue
        token = str(entry.pop(LEGACY_ENTRY_KEY, "") or "").strip()
        source_id = str(entry.get("id") or "").strip()
        if token and source_id:
            found[source_id] = token
    if not found:
        return False
    existing = load_tokens(data_dir)
    # `existing` last: the token file is authoritative.
    save_tokens(data_dir, {**found, **existing})
    logger.info(
        "token_store: moved %d source token(s) out of preferences.json into %s",
        len(found), TOKEN_FILENAME,
    )
    return True


def strip_tokens(roster: object) -> bool:
    """Remove every `token` key from a roster list, in place.

    Returns True when one was actually there. Used on both sides of
    `/v1/preferences` so a token can neither be served to the browser nor
    written back from it — see `server/routes/preferences.py`.
    """
    if not isinstance(roster, list):
        return False
    stripped = False
    for entry in roster:
        if isinstance(entry, dict) and LEGACY_ENTRY_KEY in entry:
            del entry[LEGACY_ENTRY_KEY]
            stripped = True
    return stripped
