"""Concurrent fetch of one `/v1` GET path from N sources.

The contract that matters: **a source being unreachable is expected**. The
whole point of a merged view across a bench machine and a server on the tailnet
is that one of them is often asleep. So every failure mode here — connect
refused, timeout, 500, non-JSON body — is caught per source and recorded as an
error entry in that source's `SourceResult`. `fetch_path` itself never raises
for a source-side failure, so one dead source degrades the merged view instead
of failing it.

The local source is served **in-process** through the `inventory_api` facade,
never over HTTP to ourselves: looping back through our own uvicorn would burn a
worker thread, would deadlock the moment the fan-out ran from inside a request
handler on a single-worker server, and would need credentials for our own auth
middleware. The caller passes a `local_fetch` callable, so this module stays
ignorant of the facade's method surface.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import anyio

from server.proxy import SOURCE_HEADER
from server.sources import LOCAL_ID, Source, SourceClients

logger = logging.getLogger(__name__)

# Per-source ceiling for a fan-out leg. Shorter than a proxied request's: a
# merged read waits on the slowest source, and a stalled one must not hold the
# whole view hostage.
FANOUT_TIMEOUT_SECONDS = 8.0


@dataclass(frozen=True)
class SourceResult:
    """One source's contribution to a fan-out. `ok` is the only thing to branch on."""

    source: Source
    ok: bool
    data: Any = None
    error: str = ""
    status: int | None = None

    def as_status(self) -> dict[str, Any]:
        """The JSON-able per-source status a merged response reports alongside data."""
        return {
            "id": self.source.id,
            "name": self.source.name,
            "ok": self.ok,
            "error": self.error,
            "status": self.status,
        }


async def _fetch_one(
    source: Source,
    path: str,
    *,
    clients: SourceClients,
    local_fetch: Callable[[], Any] | None,
    extract: Callable[[Any], Any] | None,
    params: Any,
    timeout: float,
    out: dict[str, SourceResult],
) -> None:
    try:
        if source.is_local:
            if local_fetch is None:
                raise RuntimeError(
                    "the local source was included in a fan-out with no local_fetch callable"
                )
            # The facade is sync and takes InventoryApi._lock; hand it to a
            # worker thread so it cannot block the event loop (nor the other
            # sources' in-flight requests).
            data = await anyio.to_thread.run_sync(local_fetch)
            out[source.id] = SourceResult(
                source=source,
                ok=True,
                data=extract(data) if extract is not None else data,
                status=200,
            )
            return

        response = await clients.get(source).get(
            path,
            params=params,
            timeout=timeout,
            # Pin the peer to its OWN data. Every desktop dubIS is itself a hub
            # with its own saved default, so an unpinned `GET /v1/parts` to
            # `bench` can come back holding SHOP's inventory — labelled bench in
            # our tab, our badge and our breakdown, and counted a second time in
            # a merged view whose total is then confidently, invisibly too high.
            # Conservation failing upward is the one direction no partial-view
            # banner will ever mention, because nothing failed.
            headers={SOURCE_HEADER: LOCAL_ID},
        )
        if not 200 <= response.status_code < 300:
            # Not `>= 400`: a 302 to an SSO login page is a source that did not
            # answer the question either, and `follow_redirects` is off.
            out[source.id] = SourceResult(
                source=source,
                ok=False,
                error=f"HTTP {response.status_code}",
                status=response.status_code,
            )
            return
        payload = response.json()
        out[source.id] = SourceResult(
            source=source,
            ok=True,
            data=extract(payload) if extract is not None else payload,
            status=response.status_code,
        )
    except Exception as exc:  # noqa: BLE001 — a down source degrades, never fails
        logger.info("fanout: %s (%s) failed on %s: %s", source.id, source.url, path, exc)
        out[source.id] = SourceResult(
            source=source, ok=False, error=f"{type(exc).__name__}: {exc}",
        )


async def fetch_path(
    sources: Iterable[Source],
    path: str,
    *,
    clients: SourceClients,
    local_fetch: Callable[[], Any] | None = None,
    extract: Callable[[Any], Any] | None = None,
    params: Any = None,
    timeout: float = FANOUT_TIMEOUT_SECONDS,
) -> list[SourceResult]:
    """Fetch *path* from every source concurrently. Results keep the input order.

    *extract* turns a source's raw body into the payload the caller wants, and —
    this is the point of it living here rather than in the caller — it may
    **raise** on a body it does not recognize. That raise lands in the same
    per-source handler as a refused connection, so "answered with something that
    is not an inventory" becomes an ordinary `ok: false` with a reason instead of
    a success that contributes nothing. There is exactly one place where "did
    this source answer the question" is decided, and it is here.
    """
    ordered: Sequence[Source] = list(sources)
    out: dict[str, SourceResult] = {}
    async with anyio.create_task_group() as tg:
        for source in ordered:
            # anyio's start_soon passes positional args only, hence the partial.
            tg.start_soon(
                functools.partial(
                    _fetch_one,
                    source,
                    path,
                    clients=clients,
                    local_fetch=local_fetch,
                    extract=extract,
                    params=params,
                    timeout=timeout,
                    out=out,
                ),
                name=f"fanout:{source.id}",
            )
    return [out[source.id] for source in ordered]
