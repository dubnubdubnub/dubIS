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
    api = request.app.state.api
    return api.load_preferences()


@router.put("/preferences", operation_id="save_preferences")
def save_preferences(request: Request, body: dict = Body(...)) -> dict:
    api = request.app.state.api
    current = dict(api.load_preferences() or {})

    incoming = dict(body or {})
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
