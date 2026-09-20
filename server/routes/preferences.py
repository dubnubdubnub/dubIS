"""Preferences /v1 routes.

Preferences are free-form user-configurable settings (thresholds, column
choices, filters, etc.) persisted to `data/preferences.json`. Unlike the
inventory-mutating routes elsewhere in `server/routes/`, saving preferences
does not touch inventory-derived state, so there is no `finish_mutation`
call and no `inventory.updated` publish here.

## Why `PUT` merges instead of replacing

It used to replace the whole file, and that was correct while only one dubIS
window could exist: the data-dir lock guaranteed it. `app_launch.py`'s attached
mode ends that — a second launch serves its window from the first window's hub —
and this file is now shared by both windows *and* carries per-window state
(`server_tabs`, `active_tab`).

`js/store.js`'s `savePreferences` posts the entire in-memory object, loaded when
that window started. With a whole-file replace, every save is a wholesale revert
of everything the other window has done since it started. Two clicks apart:
window A adds a server, window B (open since before that) opens a tab, and A's
server is gone from disk — A's next request naming it answers 404.

So a `PUT` now merges at the top level: keys present in the body are written,
keys absent are left alone. Two windows editing different keys both survive.

Consequences worth stating, because they are the contract the frontend has to
hold:

* **A key cannot be deleted by omitting it.** Send the empty value (`[]`, `{}`,
  `""`) instead. A blind replace is what made deletion-by-omission work, and it
  is exactly what cost the other window its work.
* **Merging is one level deep.** Two windows editing *the same* key still race,
  last writer winning that key. Per-key is the granularity the data has —
  `server_tabs` is one list, not a mergeable set — so anything finer would be
  inventing a merge the frontend has not defined. The remaining exposure is a
  window posting its whole startup copy of a key another window has since
  changed, and the answer to that is for the client to send only what it
  changed, which per-key merging is what makes possible.
* **The roster has a better route.** `servers`, `active_source` and `server_url`
  are still writable here — the frontend round-trips its whole preferences
  object — but `/v1/sources` is the writer that validates them, and a `PUT` that
  changes one of them is logged so the bypass is visible.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Request

from server import token_store
from server.sources import ACTIVE_KEY, ROSTER_KEY, SERVER_URL_KEY

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["preferences"])

# Keys that live in preferences.json but whose *validated* writer is
# `/v1/sources` — it checks the URL shape, duplicate ids and urls, the selector
# grammar and the reserved ids, and keeps `server_url` consistent with the
# default. Writing them through here still works (the frontend posts its whole
# preferences object), but it bypasses all of that, so a write that changes one
# is logged: it is the one remaining way a stale copy of the roster can undo
# another window's edit, and the fix for that is the frontend sending only the
# keys it changed.
SOURCE_OWNED_KEYS = frozenset({ROSTER_KEY, ACTIVE_KEY, SERVER_URL_KEY})


@router.get("/preferences", operation_id="load_preferences")
def load_preferences(request: Request) -> dict:
    """The whole preferences object — minus any source bearer token.

    Tokens live in `<data_dir>/server_tokens.json` (server/token_store.py), so
    normally there is nothing here to remove. The strip is the belt to that
    file's braces, and it covers the one case the file cannot: a
    `preferences.json` written by the build that DID store tokens in the roster,
    read before anything has triggered the migration. Without it, that user's
    credentials are handed to the browser — console-readable, and visible in the
    DevTools network pane — on the app's very first request.
    """
    api = request.app.state.api
    prefs = dict(api.load_preferences() or {})
    roster = prefs.get(ROSTER_KEY)
    if isinstance(roster, list):
        # Copy before stripping: `api.load_preferences()` may hand back a cached
        # object, and mutating it would delete the token the registry needs to
        # reach that source at all.
        roster = [dict(entry) if isinstance(entry, dict) else entry for entry in roster]
        if token_store.strip_tokens(roster):
            logger.info(
                "preferences: stripped source token(s) from GET /v1/preferences — a "
                "credential is never served to a client. They will move to %s on the "
                "next registry read.", token_store.TOKEN_FILENAME,
            )
        prefs[ROSTER_KEY] = roster
    return prefs


@router.put("/preferences", operation_id="save_preferences")
def save_preferences(request: Request, body: dict = Body(...)) -> dict:
    api = request.app.state.api
    current = dict(api.load_preferences() or {})

    incoming = dict(body or {})
    # The frontend posts its whole in-memory preferences object, and its roster
    # loader (`normalizeServers` in js/servers-logic.js) knows only
    # `{id, name, url}`. Before tokens moved to their own file, that round trip
    # ERASED every one of them — a nudge of any unrelated slider was enough, and
    # the symptom was a server that had worked yesterday 401ing today with
    # nothing on screen to say why.
    #
    # They cannot be erased now, because they are not in this file to erase. The
    # strip below closes the other direction: `/v1/sources` is the only writer
    # of a credential, so one arriving here — from a stale client, or a hand
    # rolled PUT — is dropped rather than persisted back into the file we just
    # spent the effort keeping clean.
    if token_store.strip_tokens(incoming.get(ROSTER_KEY)):
        logger.warning(
            "preferences: PUT /v1/preferences carried a source token in %r — dropped. "
            "Tokens are written only by POST/PATCH /v1/sources, into %s.",
            ROSTER_KEY, token_store.TOKEN_FILENAME,
        )
    unvalidated = sorted(
        key for key in incoming
        if key in SOURCE_OWNED_KEYS and incoming[key] != current.get(key)
    )
    if unvalidated:
        logger.info(
            "preferences: PUT /v1/preferences changed %s, bypassing the validation in "
            "/v1/sources — prefer that route for the roster and the default source",
            unvalidated,
        )

    current.update(incoming)
    api.save_preferences(current)
    return {"ok": True}
