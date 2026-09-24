"""Pick which configured server source the hub serves a request from.

The local hub routes every `/v1` request by its `X-Dubis-Source` header
(`server/dispatch.py`): one source id, `local`, `merged`, or a comma-joined set
of ids. A request that sends none is served from the hub's *persisted default*,
which is whatever a window last saved — fine for "whatever I normally look at",
wrong for "compare against reality-labs", and changing that default
(`dubis sources set-active`) to answer one read rewrites saved preferences.

`resolve_server` turns what a human types (`reality-labs`, `fremont`, an id, or
a comma list of them) into the id selector the hub accepts, using the hub's own
roster (`GET /v1/sources`). An unknown or ambiguous name raises
`ServerSelectionError` instead of being sent as-is or dropped: the hub would
404 an unknown single id, but it would quietly serve a *merged* read from
local for a group it cannot fully resolve on a non-parts route, and a
selection that silently lands somewhere else is the failure this exists to
prevent.
"""

from __future__ import annotations

from dataclasses import dataclass

from .v1client import V1Client, V1Error

SOURCE_HEADER = "X-Dubis-Source"
LOCAL = "local"
MERGED = "merged"
PASSTHROUGH = frozenset({LOCAL, MERGED})
SEPARATOR = ","


class ServerSelectionError(Exception):
    """`--server` / `DUBIS_SERVER` named something the hub has no source for.

    The CLI maps this to exit 3 — the same class as a server-rejected request —
    and never falls back to the hub's default source.
    """


@dataclass(frozen=True)
class ServerSelection:
    """What the caller asked for and what it resolved to.

    ``selector`` is the exact `X-Dubis-Source` value sent. ``names`` pairs each
    resolved id with its roster name, so `dubis status` can say which server
    answered in words a human recognizes. ``via`` is ``"flag"`` or ``"env"``.
    """

    requested: str
    selector: str
    names: tuple[tuple[str, str], ...]
    via: str

    @property
    def is_view(self) -> bool:
        """A merge (``merged`` or a set) rather than one source."""
        return self.selector == MERGED or SEPARATOR in self.selector


def _norm(value: str) -> str:
    return value.strip().casefold().replace("_", "-").replace(" ", "-")


def _roster(client: V1Client) -> list[dict]:
    try:
        body = client.get("/v1/sources")
    except V1Error as exc:
        raise ServerSelectionError(
            f"could not list the hub's sources to resolve the server selection: {exc}"
        ) from exc
    sources = body.get("sources") if isinstance(body, dict) else None
    if not isinstance(sources, list):
        raise ServerSelectionError(
            "GET /v1/sources did not answer with a source roster; cannot resolve "
            "the server selection"
        )
    return sources


def _describe(roster: list[dict]) -> str:
    known = [f"{s.get('id')} ({s.get('name')})" for s in roster]
    return ", ".join(known + [LOCAL, MERGED])


def _resolve_token(token: str, roster: list[dict]) -> tuple[str, str]:
    lowered = token.casefold()
    if lowered in PASSTHROUGH:
        return lowered, ("Local" if lowered == LOCAL else "merged")

    exact = {s["id"]: s for s in roster if token in (s.get("id"), s.get("name"))}
    matches = exact or {
        s["id"]: s for s in roster
        if _norm(token) in (_norm(str(s.get("id", ""))), _norm(str(s.get("name", ""))))
    }
    if not matches:
        raise ServerSelectionError(
            f"unknown server {token!r}: the hub has no source with that id or name. "
            f"Known: {_describe(roster)}"
        )
    if len(matches) > 1:
        options = ", ".join(f"{sid} ({s.get('name')})" for sid, s in sorted(matches.items()))
        raise ServerSelectionError(
            f"server {token!r} is ambiguous — it matches {options}; name one by id"
        )
    (source_id, source), = matches.items()
    return source_id, str(source.get("name") or source_id)


def resolve_server(client: V1Client, requested: str, via: str) -> ServerSelection:
    """Resolve *requested* against the hub's roster into an `X-Dubis-Source` value.

    `local` and `merged` pass through without a roster lookup. Anything else —
    each comma-separated member of a set, too — must match exactly one source by
    id or name (exact first, then case/`_`/`-`-insensitive), or this raises.
    """
    tokens = [t.strip() for t in requested.split(SEPARATOR) if t.strip()]
    if not tokens:
        raise ServerSelectionError(f"empty server selection {requested!r}")

    roster: list[dict] | None = None
    resolved: list[tuple[str, str]] = []
    for token in tokens:
        if token.casefold() not in PASSTHROUGH and roster is None:
            roster = _roster(client)
        resolved.append(_resolve_token(token, roster or []))

    return ServerSelection(
        requested=requested,
        selector=SEPARATOR.join(sid for sid, _ in resolved),
        names=tuple(resolved),
        via=via,
    )


def select_server(client: V1Client, requested: str, via: str) -> ServerSelection:
    """Resolve *requested* and pin *client* to it for every later request."""
    selection = resolve_server(client, requested, via)
    client.use_source(selection)
    return selection
