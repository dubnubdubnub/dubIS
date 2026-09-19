"""Inventory-mirror /v1 routes.

The mirror is a read-only, Tailscale-served snapshot of the inventory, backed
by a daemon that these routes install on / remove from the machine running the
server (`domain/api_mirror.py`'s `MirrorFacade` plus `mirror_install/`). None
of that is inventory-derived state, so — exactly like `preferences.py` — no
route here calls `finish_mutation` or publishes `inventory.updated`.

Enable/disable are POSTs to `/enable` and `/disable` sub-paths rather than one
`PUT /v1/inventory-mirror {"enabled": bool}` so each `operation_id` keeps the
name of the `InventoryApi` method it dispatches to
(`enable_inventory_mirror` / `disable_inventory_mirror`) — the naming contract
`tests/python/server/test_v1_surface.py` enforces, and the name
`js/preferences-modal.js` already calls. All three return the same shape,
which is what `MirrorFacade` already does.

`MirrorFacade` signals a host that *cannot* mirror by raising, in two flavors:
`RuntimeError` — from `tailscale.enable_serve` when tailscale is missing or not
logged in, and from the installers themselves (e.g. `LinuxInstaller` when
`systemctl --user` fails, which is how the container image reports it: it *is*
Linux, so it gets a real installer and fails at systemd, not at dispatch) — and
`NotImplementedError` from `base.get_installer` on a platform that is none of
win32/darwin/linux. Both messages are written to be shown to the user, so they
are re-raised as `DubISError` to travel in the standard `{error, code, detail}`
body instead of being swallowed by an unhandled-500; `server/errors.py` has no
status closer than its generic 500, and the message, not the code, is what the
Preferences modal surfaces. Reading the info needs no such guard —
`get_inventory_mirror_info` already degrades a missing installer to
`installed: false, running: false`.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from dubis_errors import DubISError

router = APIRouter(prefix="/v1", tags=["mirror"])


@router.get("/inventory-mirror", operation_id="get_inventory_mirror_info")
def get_inventory_mirror_info(request: Request) -> dict:
    api = request.app.state.api
    return api.get_inventory_mirror_info()


@router.post("/inventory-mirror/enable", operation_id="enable_inventory_mirror")
def enable_inventory_mirror(request: Request) -> dict:
    api = request.app.state.api
    try:
        return api.enable_inventory_mirror()
    except (RuntimeError, NotImplementedError) as exc:
        raise DubISError(str(exc)) from exc


@router.post("/inventory-mirror/disable", operation_id="disable_inventory_mirror")
def disable_inventory_mirror(request: Request) -> dict:
    api = request.app.state.api
    try:
        return api.disable_inventory_mirror()
    except (RuntimeError, NotImplementedError) as exc:
        raise DubISError(str(exc)) from exc
