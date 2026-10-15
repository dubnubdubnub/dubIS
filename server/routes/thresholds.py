"""`/v1/thresholds` — the server's threshold profile as a snapshot history.

The profile a server stocks-value-colours with is no longer only its build's
defaults: it is a *history* (domain/threshold_snapshots.py), snapshot 0 being
the shipped default profile itself, and every write appending a new snapshot.
Undo appends a copy of the previous snapshot and activates that — history is
append-only and undo is never destructive.

## Why a new route instead of `/v1/preferences`

`/v1/preferences` is in `server/proxy.LOCAL_ONLY_PATHS` and must stay there:
the hub never forwards it, and `savePreferences` posts the whole in-memory
bag — serving new server-side state through it would persist defaults as user
state on the next unrelated save (the exact reason presets ride on `/v1/meta`
and never through preferences, documented in js/store.js and
domain/thresholds.py). A per-server profile is *server* state, so it gets a
per-server route: proxyable, addressed by `X-Dubis-Source` like every other
data route.

## Routing, auth, and the merged refusal — all inherited, none invented

* **Per-source**: the route is NOT local-only, so `X-Dubis-Source: shop`
  proxies the write to `shop` (server/dispatch.py). A window on a `shop` tab
  tunes *shop's* profile, not the hub's.
* **Merged/grouped writes are refused 400** by `Registry.resolve` before the
  handler ever runs — the same `source_config` refusal every other write gets
  on a view selector ("this write must name the single source that serves
  it"). Nothing here re-implements it.
* **Anonymous is refused 401** by `AuthMiddleware` in `DUBIS_AUTH_MODE=on`:
  writes are allowed from any authenticated caller — bearer token, tailnet
  header, Unix-socket peer, or the loopback desktop (`local`, which is what
  the whole trust model has always treated as the operator). In auth-`off`
  mode there is no identity to check (the loopback trust model), exactly as
  for every other mutation.
* **Attribution**: every snapshot records `created_by` as the composed
  `source@identity` (`server/auth.stamp_source`) the adjustment ledger uses.

## No SSE publish

These writes change no inventory row, so there is no `finish_mutation` /
`inventory.updated` publish here — the same call the preferences route makes
for the same reason. Repainting open windows on a profile change is the
client-state PR's job (it will read this route), not something to smuggle
through an inventory event that would force every window to refetch stock.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from domain import threshold_snapshots
from server.auth import stamp_source
from server.models import (
    ActivateThresholdSnapshotBody,
    CreateThresholdSnapshotBody,
    ThresholdsResponse,
    UndoThresholdSnapshotBody,
)

router = APIRouter(prefix="/v1", tags=["thresholds"])


def _payload(request: Request, history: threshold_snapshots.History) -> dict:
    """The history, the active snapshot, and the resolved server-side profile.

    `resolved` runs the server half of the resolution spec (active snapshot ->
    snapshot 0 -> derivation -> base) over every category this server serves;
    client overrides and presets sit above it and are the client's to apply
    (domain/thresholds.py states the whole order).
    """
    api = request.app.state.api
    active = history.active
    return {
        "snapshots": [snapshot.as_dict() for snapshot in history.snapshots],
        "active_id": active.id,
        "active": active.as_dict(),
        "resolved": threshold_snapshots.resolved_profile(
            active, api.DEFAULT_THRESHOLDS, api.FLAT_SECTION_ORDER,
        ),
    }


@router.get("/thresholds", response_model=ThresholdsResponse,
            operation_id="list_threshold_snapshots")
def list_threshold_snapshots(request: Request) -> dict:
    """The snapshot list, the active one, and the resolved profile.

    A fresh server answers exactly one snapshot — id 0, the shipped defaults
    — because snapshot 0 is derived, not stored: there is no migration and no
    seeded file, and `default_thresholds` on `/v1/meta` is that same table.
    """
    api = request.app.state.api
    history = threshold_snapshots.load(api.base_dir, api.DEFAULT_THRESHOLDS)
    return _payload(request, history)


@router.put("/thresholds", response_model=ThresholdsResponse,
            operation_id="create_threshold_snapshot")
def create_threshold_snapshot(
    request: Request, body: CreateThresholdSnapshotBody,
) -> dict:
    """Append a new active snapshot, from a full profile or a sparse delta.

    `delta` (default) merges `values` onto the active snapshot's values — a
    null value removes a category from the new state, which is how a tuned
    category is handed back to snapshot 0 / derivation. `full` records the
    given profile as-is. Either way the previous snapshots are untouched:
    this is an append, not an edit.

    Refused: 401 anonymous (auth-on), 400 `source_config` on a merged/grouped
    selector (the dispatch middleware's write rule), 400 `value_error` for a
    category this server does not serve or a non-integer/negative threshold.
    """
    api = request.app.state.api
    history, _new = threshold_snapshots.create(
        api.base_dir,
        api.DEFAULT_THRESHOLDS,
        values=body.values,
        mode=body.mode,
        created_by=stamp_source(request, body.source),
        note=body.note,
        known_sections=set(api.FLAT_SECTION_ORDER),
    )
    return _payload(request, history)


@router.post("/thresholds/undo", response_model=ThresholdsResponse,
             operation_id="undo_threshold_snapshot")
def undo_threshold_snapshot(request: Request, body: UndoThresholdSnapshotBody) -> dict:
    """Append a copy of the previous snapshot and make it active.

    Never destructive — the undone snapshot stays in the record exactly as it
    was. With snapshot 0 active there is no earlier state, and that is a loud
    409 `nothing_to_undo`, never a 200 that quietly changed nothing: a caller
    who asked to undo must never be told "done" when nothing was.
    """
    api = request.app.state.api
    history, _new = threshold_snapshots.undo(
        api.base_dir,
        api.DEFAULT_THRESHOLDS,
        created_by=stamp_source(request, body.source),
        note=body.note,
    )
    return _payload(request, history)


@router.post("/thresholds/activate", response_model=ThresholdsResponse,
             operation_id="activate_threshold_snapshot")
def activate_threshold_snapshot(
    request: Request, body: ActivateThresholdSnapshotBody,
) -> dict:
    """Make an older snapshot's profile active — by appending a copy of it.

    Same append-a-copy semantics as undo: the activation is itself an event
    and belongs in the record; flipping `active` onto an old row would rewrite
    history's shape. Unknown id -> 404; the already-active id -> 400 (a clone
    that changes nothing is a caller mistake worth hearing about).
    """
    api = request.app.state.api
    history, _new = threshold_snapshots.activate(
        api.base_dir,
        api.DEFAULT_THRESHOLDS,
        body.snapshot_id,
        created_by=stamp_source(request, body.source),
        note=body.note,
    )
    return _payload(request, history)
