"""The hub's source registry: which dubIS servers this server federates.

See `docs/plans/2026-09-19-multi-server-hub-design.md`. The local server is
always a hub: it always serves the window, and every *other* dubIS server is a
data *source* it fetches on the client's behalf. That keeps the browser
same-origin by construction (no CORS, no preflight, no cross-site cookie rules,
no mixed content) and is what makes a merged view possible at all.

## There is no "current server"

The hub holds **no mutable active-source state**. Which source serves a request
is decided by that request alone — `X-Dubis-Source: <id> | local | merged`, read
by `server/dispatch.py` — and the registry this module builds is rebuilt, read
only, from preferences on every read.

That is a correctness requirement, not tidiness. Several app windows are open at
once, deliberately on different servers. A server-global "active source" that one
window could flip would move another window's data underneath it mid-session:
window B, showing the shop's stock, would silently start showing the bench's
because window A clicked a tab. No variable in this module can do that, because
none of them are written by a request path.

What *is* persisted is a **default** — what a client gets when it sends no
header at all (`tools/dubis-cli`, curl, a page load before its source signal has
initialized). A default is not live state: changing it cannot move a request
that named its own source.

## Persistence — `data/preferences.json`, the hub's OWN preferences

    "servers":       [{id, name, url, token?, enabled?}, ...]   the roster
    "server_url":    "https://..."   the DEFAULT source's url, "" = local
    "active_source": "local" | "<source id>" | "merged"         the default

`servers` keeps the exact `{id, name, url}` shape `js/servers-logic.js`'s
`normalizeServers` already reads (js:10-21); `token` and `enabled` are strictly
additive optional keys that the JS validator ignores, and they are only written
when they carry a non-default value, so a roster nobody has given a token or
disabled is byte-identical to what the JS side writes today.

`active_source` keeps its key name for the same back-compat reason, but it means
"the default", and `PUT /v1/sources/active` is a *preference write*, not a
switch. See that route's docstring.

`server_url` keeps meaning exactly what `remote_mode.resolve_remote_base_url`
and `tools/dubis-cli` already assume — "the single remote server my data comes
from" — so neither had to learn about the roster or about `active_source`. It is
written as a derivation of the default:

  * default = `local`   -> `server_url` = `""`   (today's local mode)
  * default = `<id>`    -> `server_url` = that source's url
  * default = `merged`  -> `server_url` = `""`

`merged` deliberately writes `""`: there is no *single* remote url for a merged
view, and `""` is the honest answer for a reader that can only hold one. It
sends `remote_mode` and `tools/dubis-cli` to the hub itself (env `DUBIS_URL`,
then `<data_dir>/.v1_port`), which is precisely where the merged view is served
from — so the CLI sees the merge rather than one arbitrary member of it.

Read back, `active_source` wins when it names something real. When it is absent
(a preferences file written before this feature, or by the JS server picker,
which only writes `server_url`) the default is *derived* from `server_url`: the
roster entry with that url, or — when no entry matches — a synthetic, unpersisted
entry for it, so a `DUBIS_URL`/`server_url` pointing at a server that was never
added to the roster still selects that server instead of being silently ignored.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import threading
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import httpx

from dubis_errors import SourceConfigError, SourceNotFoundError
from server import token_store

logger = logging.getLogger(__name__)

LOCAL_ID = "local"
MERGED_ID = "merged"
# Neither may be used as a roster entry's id: `local` names the implicit entry
# synthesized below (js/servers-logic.js reserves it for the same reason) and
# `merged` names the fan-out pseudo-target of `active_source`.
RESERVED_IDS = frozenset({LOCAL_ID, MERGED_ID})

# A selector value — the `X-Dubis-Source` header, and the persisted default —
# is one id, several ids separated by this, or the keyword `merged`. A source id
# may therefore not contain it: with sources `a`, `b` and `a,b` configured, the
# selector `a,b` would silently mean the merge instead of the source actually
# named, and nothing in the answer would say so.
SELECTOR_SEPARATOR = ","


def split_selectors(raw: str | None) -> list[str]:
    """`"bench, shop"` -> `["bench", "shop"]`. Empty list when unset/blank."""
    return [part.strip() for part in (raw or "").split(SELECTOR_SEPARATOR) if part.strip()]

ROSTER_KEY = "servers"
ACTIVE_KEY = "active_source"
SERVER_URL_KEY = "server_url"

# Deliberately short: `GET /v1/sources` probes every source in parallel and the
# whole response must stay snappy enough to render a tab strip's dots.
PROBE_TIMEOUT_SECONDS = 2.0
# The ceiling for a proxied/fanned-out request, not for a probe.
REQUEST_TIMEOUT_SECONDS = 30.0

# Serializes the read-modify-write of preferences.json that every roster/active
# mutation performs. FastAPI runs sync route handlers on a thread pool, so two
# concurrent `POST /v1/sources` calls really can interleave.
_write_lock = threading.Lock()

# The launch-time default (`app_launch.ACTIVE_SOURCE_SEAM`). `DUBIS_URL` outranks
# preferences and is deliberately never written to them — `app_restart.py` strips
# it from a relaunch so a one-off override cannot outlive the session — so it
# cannot travel through `server_url` and has to be held in process.
#
# This is configuration, not state: it is written exactly once, on the boot
# thread, before `create_app`, and no request path ever writes it. It therefore
# cannot be what moves one window's data because of what another window did —
# the failure mode this whole module is arranged to make impossible.
_boot_default_url: str = ""
# `DUBIS_TOKEN`, the credential for `_boot_default_url`. Same lifecycle, same
# reasoning: a one-off override `app_restart.py` strips from a relaunch, so it
# has no home on disk either. Never logged.
_boot_default_token: str = ""


@dataclass(frozen=True)
class Source:
    """One dubIS server this hub can read. `local` is this server itself."""

    id: str
    name: str
    url: str = ""
    token: str = ""
    enabled: bool = True
    # True for an entry `load_registry` conjured from `server_url`/`DUBIS_URL`
    # rather than read from the roster. It is addressable and fannable, but it
    # is not in `servers`, so editing or removing it would write a roster that
    # never contained it — refused in `update_source`/`remove_source`.
    synthetic: bool = False

    @property
    def is_local(self) -> bool:
        return self.id == LOCAL_ID


LOCAL_SOURCE = Source(id=LOCAL_ID, name="Local", url="", token="", enabled=True)


def normalize_url(raw: Any) -> str:
    """Canonicalize a source url, or return "" if it is unusable.

    Mirrors `normalizeServerUrl` in js/servers-logic.js exactly, so the two
    halves of the roster can never disagree about what a stored entry means: a
    scheme is mandatory (a scheme-less url would resolve against the hub's own
    origin, silently pointing back at the hub), and trailing slashes are
    stripped so `https://x` and `https://x/` are one entry, not two.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    if not re.match(r"^https?://", text, re.IGNORECASE):
        return ""
    return text.rstrip("/")


def name_from_url(url: str) -> str:
    """The host (with port) of a normalized url — the default display name."""
    normalized = normalize_url(url)
    if not normalized:
        return ""
    return re.sub(r"^https?://", "", normalized, flags=re.IGNORECASE).split("/")[0]


def _id_from_url(url: str, taken: set[str]) -> str:
    """A stable, readable id derived from a url's host, unique within *taken*."""
    base = re.sub(r"[^a-z0-9]+", "-", name_from_url(url).lower()).strip("-") or "source"
    if base not in taken and base not in RESERVED_IDS:
        return base
    n = 2
    while f"{base}-{n}" in taken or f"{base}-{n}" in RESERVED_IDS:
        n += 1
    return f"{base}-{n}"


def _entry_to_source(
    entry: Any, seen_ids: set[str], seen_urls: set[str],
    tokens: Mapping[str, str] | None = None,
) -> Source | None:
    """Validate one persisted roster entry, or return None and warn.

    Same contract as `normalizeServers` in js/servers-logic.js: preferences.json
    is a file a user may reasonably hand-edit, so a malformed entry is dropped
    with a warning rather than crashing every request that reads the roster.
    """
    if not isinstance(entry, dict):
        logger.warning("sources: ignoring non-object servers entry")
        return None
    source_id = str(entry.get("id") or "").strip()
    if not source_id:
        logger.warning("sources: ignoring servers entry with missing/invalid id")
        return None
    if source_id in RESERVED_IDS:
        logger.warning("sources: ignoring servers entry using the reserved id %r", source_id)
        return None
    if SELECTOR_SEPARATOR in source_id:
        logger.warning(
            "sources: ignoring servers entry %r — a source id may not contain the "
            "selector separator %r, it would be unaddressable",
            source_id, SELECTOR_SEPARATOR,
        )
        return None
    if source_id in seen_ids:
        logger.warning("sources: ignoring servers entry with duplicate id %r", source_id)
        return None
    url = normalize_url(entry.get("url"))
    if not url:
        logger.warning("sources: ignoring servers entry %r whose URL is not http(s)", source_id)
        return None
    if url in seen_urls:
        logger.warning("sources: ignoring servers entry %r duplicating URL %s", source_id, url)
        return None
    name = str(entry.get("name") or "").strip() or name_from_url(url)
    # The bearer token lives in `<data_dir>/server_tokens.json`, never in the
    # roster — see server/token_store.py for why. `entry["token"]` is still read
    # as a fallback so a preferences file written by the build that DID store it
    # there keeps working until `migrate_legacy_tokens` rewrites it.
    token = str((tokens or {}).get(source_id) or entry.get("token") or "")
    enabled = entry.get("enabled")
    seen_ids.add(source_id)
    seen_urls.add(url)
    return Source(
        id=source_id,
        name=name,
        url=url,
        token=token,
        enabled=True if enabled is None else bool(enabled),
    )


def _source_to_entry(source: Source) -> dict[str, Any]:
    """Serialize back to the persisted shape.

    **The token is deliberately not here.** It is written to
    `<data_dir>/server_tokens.json` by `_persist`, so `preferences.json` — the
    file `GET /v1/preferences` hands to the browser and `js/store.js` posts back
    wholesale — never contains a credential at all. See server/token_store.py.

    `enabled` is written only when false, so a roster nobody has disabled stays
    byte-identical to what the JS server picker writes.
    """
    entry: dict[str, Any] = {"id": source.id, "name": source.name, "url": source.url}
    if not source.enabled:
        entry["enabled"] = False
    return entry


@dataclass(frozen=True)
class MergeSet:
    """The sources a merged view covers, split into the ones it will fetch and
    the ones it is deliberately leaving out.

    `excluded` is the load-bearing half. A merged total that is missing a whole
    machine is a *smaller perfectly plausible number* — there is no shape to it
    — so a source left out has to be NAMED in the answer with the reason, not
    quietly absent from it. `server/dispatch.py` reports each excluded source as
    a failed entry, which is what the partial-view banner reads.
    """

    included: tuple[Source, ...]
    excluded: tuple[tuple[Source, str], ...] = ()

@dataclass(frozen=True)
class Registry:
    """The roster plus the persisted DEFAULT. Immutable; rebuilt on every read.

    `default` is not "the current server". It is what a request that names no
    source falls back to. Nothing here is mutable and nothing here is shared
    between requests — see the module docstring for why that matters with
    several windows open.
    """

    sources: tuple[Source, ...]  # local first, then the persisted roster
    default: str  # LOCAL_ID | a source id | MERGED_ID

    def get(self, source_id: str) -> Source | None:
        for source in self.sources:
            if source.id == source_id:
                return source
        return None

    def require(self, source_id: str) -> Source:
        source = self.get(source_id)
        if source is None:
            known = ", ".join(s.id for s in self.sources)
            raise SourceNotFoundError(f"unknown source {source_id!r}; known sources: {known}")
        return source

    @property
    def remotes(self) -> tuple[Source, ...]:
        return tuple(s for s in self.sources if not s.is_local)

    @property
    def enabled_sources(self) -> tuple[Source, ...]:
        """What `merged` fans out to: local plus every enabled remote."""
        return tuple(s for s in self.sources if s.enabled)


    @property
    def default_selectors(self) -> list[str]:
        """The persisted default, parsed with the SAME grammar as the header.

        One vocabulary: every selector the tab strip can emit — `shop`,
        `bench,shop`, `merged` — is a selector both `X-Dubis-Source` and
        `PUT /v1/sources/active` accept. A default that only the header
        understood would 404 every grouped tab click and leave `server_url`
        naming whichever single server was picked before it.
        """
        return split_selectors(self.default)

    @property
    def default_source(self) -> Source | None:
        """The one default source, or None when the default names a view."""
        selectors = self.default_selectors
        if len(selectors) != 1 or selectors[0] == MERGED_ID:
            return None
        return self.require(selectors[0])

    @property
    def default_is_merged(self) -> bool:
        return self.default == MERGED_ID

    @property
    def default_url(self) -> str:
        """What `server_url` must hold for this registry — see module docstring.

        A view (`merged`, or a group) has no single remote url, so it writes
        `""` and sends `remote_mode`/`tools/dubis-cli` to the hub itself, which
        is where that view is served from.
        """
        source = self.default_source
        return source.url if source is not None else ""

    def resolve(
        self, selectors: Sequence[str], *, mergeable: bool, explicit: bool,
    ) -> Source | MergeSet:
        """Selectors -> what serves this request.

        Returns **a `Source`** when one server answers, or **a `MergeSet`** when
        the answer is a merge. A `MergeSet` is never a suggestion to pick one of
        its members.

        Pure: the same arguments always give the same answer, which is what lets
        `server/dispatch.py` be a function of the request rather than of server
        state.

        The three shapes a caller can ask for, on the header and on
        `PUT /v1/sources/active` alike — one vocabulary, because the tab strip is
        the only thing that generates them:

        * `["shop"]`          — that one server, directly addressed.
        * `["bench", "shop"]` — a merge of those two. This is a browser-style tab
          *group*: ctrl-click two tabs and you get the union of those two
          servers, not of all of them.
        * `["merged"]`        — a merge of every configured source. The "All" tab.

        ## `enabled` governs views, not addressing

        A merged view — `merged` OR an enumerated group — covers only enabled
        sources, and **names the disabled ones it left out** so the answer can
        say a machine is missing. Naming a source as the *sole* selector is
        direct addressing, not a view, and serves it whatever `enabled` says:
        the user just pointed at it.

        This was the one rule with two meanings, and the reason it had to be
        reconciled is an invariant with teeth: `merged` used to mean "every
        enabled source" while a group meant "exactly these, enabled or not", so
        with `shop` disabled the group `{bench, shop}` showed MORE stock than the
        All tab that contains it — a strict superset showing less, with nothing
        on screen naming the omission. Now a view is a view: All is never beaten
        by one of its own subsets.

        A merge is returned in **roster order**, not in the order named, so the
        merge's field precedence (`domain/federation.py` resolves a scalar
        conflict by first non-empty, in source order) does not move when someone
        drags a tab.

        `mergeable` says whether this request has a merged answer at all — only
        `GET /v1/parts` does; there is no union of two servers' carts.
        `explicit` says the selectors came from an `X-Dubis-Source` header rather
        than the persisted default, and it is the difference between two
        deliberately different behaviours:

        * explicit and not mergeable -> raise. A caller that asked to merge on a
          write is asking for something with no answer, and quietly landing it on
          one arbitrary server is the wrong-answer-that-looks-right this design
          exists to remove.
        * default and not mergeable -> `local`. A window sitting on the All tab
          still has to be able to read its carts.
        """
        tokens = [str(sel).strip() for sel in selectors if str(sel).strip()]
        if not tokens:
            raise SourceConfigError("no source selector given")

        if len(tokens) == 1 and tokens[0] != MERGED_ID:
            # Direct addressing. `enabled` does not apply: the caller named it.
            return self.require(tokens[0])

        if not mergeable:
            if explicit:
                raise SourceConfigError(
                    f"{SELECTOR_SEPARATOR.join(tokens)!r} names a merged view of parts, not a "
                    "target — this request must name the single source that serves it"
                )
            return self.require(LOCAL_ID)

        if tokens == [MERGED_ID]:
            candidates = self.sources
        else:
            if MERGED_ID in tokens:
                raise SourceConfigError(
                    f"{MERGED_ID!r} already means every source and cannot be combined with "
                    f"others; name the sources you want instead: {tokens}"
                )
            duplicated = sorted({t for t in tokens if tokens.count(t) > 1})
            if duplicated:
                raise SourceConfigError(
                    f"source(s) {duplicated} named twice — a merged row's per-source "
                    "breakdown would count them twice"
                )
            # `require` raises SourceNotFoundError (404) on an unknown id rather
            # than dropping it. Silently skipping one would UNDER-REPORT stock in
            # the merged totals, which is the hardest failure here to notice and
            # the most expensive to believe.
            wanted = {self.require(token).id for token in tokens}
            candidates = tuple(s for s in self.sources if s.id in wanted)

        return MergeSet(
            included=tuple(s for s in candidates if s.enabled),
            excluded=tuple((s, "disabled") for s in candidates if not s.enabled),
        )


def load_registry(
    prefs: dict[str, Any] | None,
    boot_default_url: str = "",
    tokens: Mapping[str, str] | None = None,
    boot_default_token: str = "",
) -> Registry:
    """Build the registry from a preferences dict. Pure — no I/O.

    *boot_default_url* is the launch-time URL (`DUBIS_URL`, via
    `seed_initial_active_source`) that outranks both `server_url` and
    `active_source`. It is passed in rather than read off the module so this
    stays a pure function of its arguments.

    *tokens* is `{source_id: bearer token}`, read from
    `<data_dir>/server_tokens.json` by the caller (`load_registry_from_api`) —
    passed in for the same reason, and kept out of *prefs* entirely so the file
    the browser round-trips holds no credential. *boot_default_token* is
    `DUBIS_TOKEN`, and belongs to the synthetic source a `DUBIS_URL` launch
    conjures: without it, pointing the desktop app at an auth-on server with the
    documented `DUBIS_URL`/`DUBIS_TOKEN` pair sent no `Authorization` header at
    all and 401'd on every request.
    """
    prefs = prefs or {}
    raw_roster = prefs.get(ROSTER_KEY)
    sources: list[Source] = [LOCAL_SOURCE]
    seen_ids: set[str] = {LOCAL_ID}
    seen_urls: set[str] = set()
    if raw_roster is None:
        pass
    elif not isinstance(raw_roster, list):
        logger.warning("sources: %r is not an array — ignoring", ROSTER_KEY)
    else:
        for entry in raw_roster:
            source = _entry_to_source(entry, seen_ids, seen_urls, tokens)
            if source is not None:
                sources.append(source)

    # `server_url` is authoritative about *where data comes from* for
    # remote_mode.py / tools/dubis-cli. If it names a server the roster does not
    # list (a `DUBIS_URL`-seeded launch, or a hand-edited prefs file), adopt it
    # as a synthetic, unpersisted source rather than silently ignoring it.
    server_url = normalize_url(boot_default_url) or normalize_url(prefs.get(SERVER_URL_KEY))
    if server_url and server_url not in seen_urls:
        synthetic_id = _id_from_url(server_url, seen_ids)
        logger.info(
            "sources: %s=%s names no roster entry — adopting it as source %r",
            SERVER_URL_KEY, server_url, synthetic_id,
        )
        # A synthetic source has no roster entry, so its credential cannot come
        # from the token file (which is keyed by roster id). It comes from
        # `DUBIS_TOKEN`, alongside the `DUBIS_URL` that conjured it — or, when
        # the url came from `server_url` rather than the env, from a token file
        # entry under the id we just derived, so a hand-edited prefs file can
        # still be given one.
        synthetic_token = boot_default_token if boot_default_url else ""
        if not synthetic_token:
            synthetic_token = str((tokens or {}).get(synthetic_id) or "")
        sources.append(Source(id=synthetic_id, name=name_from_url(server_url),
                              url=server_url, token=synthetic_token, synthetic=True))
        seen_ids.add(synthetic_id)
        seen_urls.add(server_url)

    known = {s.id for s in sources}
    raw_default = "" if boot_default_url else str(prefs.get(ACTIVE_KEY) or "").strip()
    # Parsed with the selector grammar, so a grouped tab (`bench,shop`) is as
    # valid a default as a single id or `merged` — the tab strip emits all three
    # and both consumers have to take all three.
    tokens = split_selectors(raw_default)
    if tokens and all(t == MERGED_ID or t in known for t in tokens):
        default = SELECTOR_SEPARATOR.join(tokens)
    else:
        if raw_default:
            logger.warning(
                "sources: %s=%r names no known source — falling back to the default "
                "derived from %s", ACTIVE_KEY, raw_default, SERVER_URL_KEY,
            )
        default = LOCAL_ID
        if server_url:
            for source in sources:
                if source.url == server_url:
                    default = source.id
                    break

    return Registry(sources=tuple(sources), default=default)


def load_registry_from_api(api: Any) -> Registry:
    """Registry for the hub's own preferences, via the `inventory_api` facade.

    Goes through `api.load_preferences()` rather than reading
    `data/preferences.json` directly so the "other" migrations that facade
    applies (and any future one) are never bypassed — and so tests can point the
    whole thing at a tmp dir by swapping the api, exactly as every other route
    does.

    The bearer tokens come from `<data_dir>/server_tokens.json`, a second read
    of a second file — deliberately, so the credential is absent from the object
    `/v1/preferences` serves rather than merely redacted out of it. A
    preferences file still carrying tokens from the build that stored them there
    is migrated on the spot, once: the keys move to the token file and
    preferences is rewritten without them.
    """
    prefs = api.load_preferences()
    data_dir = _data_dir(api)
    if data_dir and token_store.migrate_legacy_tokens(prefs, data_dir):
        api.save_preferences(prefs)
    tokens = token_store.load_tokens(data_dir) if data_dir else {}
    return load_registry(
        prefs,
        boot_default_url=_boot_default_url,
        tokens=tokens,
        boot_default_token=_boot_default_token,
    )


def _data_dir(api: Any) -> str:
    """The data dir behind `api.prefs_json`, or "" when the api has none.

    "" rather than a guess: a test double without `prefs_json` must read as "no
    token file", not as "the process's cwd", which would make one test's tokens
    visible to the next.
    """
    prefs_json = getattr(api, "prefs_json", "")
    return os.path.dirname(prefs_json) if prefs_json else ""


def seed_initial_active_source(url: str | None, token: str | None = None) -> None:
    """Set the launch-time DEFAULT source. The seam `app_launch.py` calls at boot.

    Called once, on the server-boot thread, BEFORE `create_app`, so a client that
    sends no `X-Dubis-Source` gets `DUBIS_URL`'s server from the very first
    request. It sets a *default*, not live state: a request that names its own
    source is unaffected by it, and no request path ever writes it.

    `DUBIS_URL` cannot travel through preferences — `app_restart.relaunch_env`
    strips it precisely so a one-off override cannot outlive the session — which
    is why the default has to be held in process rather than persisted here.

    An unusable URL is logged at ERROR and dropped rather than raised. The caller
    is `app.pyw`'s boot thread: a `DUBIS_URL` typo is a user misconfiguration
    that should be loud in the log, but killing the boot thread over it would
    leave the user staring at a splash screen that times out — a worse message
    about the same mistake. The hub is fully usable on local data, which is what
    it falls back to.

    *token* is `DUBIS_TOKEN` — the credential for that URL. It rides along with
    the URL for exactly the same reason the URL cannot be persisted: it is a
    one-off override that `app_restart.py` strips from a relaunch, so it has no
    home on disk. Without it, the `DUBIS_URL`+`DUBIS_TOKEN` pair that CLAUDE.md
    documents for remote desktop mode reached an auth-on server with no
    `Authorization` header and 401'd on every request but `/v1/health` — the
    green-dot-but-401 shape, straight out of the box.

    Never logged, here or anywhere: the log line below names the URL only.
    """
    global _boot_default_url, _boot_default_token
    normalized = normalize_url(url)
    if not normalized:
        logger.error(
            "sources: refusing to take the default source from %r — a source URL must "
            "start with http:// or https://. Defaulting to local data instead.", url,
        )
        return
    _boot_default_token = str(token or "").strip()
    logger.info(
        "sources: default source seeded from %s (%s)",
        normalized, "with a token" if _boot_default_token else "no token",
    )
    _boot_default_url = normalized


def _reset_boot_default_for_tests() -> None:
    """Test-only. Nothing in the running server calls this: the boot default is
    written once, before `create_app`, and never again."""
    global _boot_default_url, _boot_default_token
    _boot_default_url = ""
    _boot_default_token = ""


# ── mutations ────────────────────────────────────────────────────────────────


def _own_port(api: Any) -> int | None:
    """This server's own bound port, from `<data_dir>/.v1_port`.

    `server/run.py` writes that file on startup (and `tools/dubis-cli` already
    discovers a running server through it), so it is the one place the hub can
    learn its own address from without being handed it. Absent — a server
    started without a data dir, or still binding — means "unknown", and the
    caller skips the check rather than guessing.
    """
    try:
        with open(os.path.join(os.path.dirname(api.prefs_json), ".v1_port"), encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _reject_self_reference(api: Any, url: str) -> None:
    """Refuse a source that is this hub itself.

    A hub listing its own address is a cycle: every fan-out asks itself for its
    own inventory, and two hubs listing each other are the same thing one hop
    further out. The general case is closed by pinning — since
    `server/proxy.py` and `server/fanout.py` now send `X-Dubis-Source: local`,
    a peer always answers from its OWN data and never re-fans-out — so this
    guard exists to catch the configuration mistake at the moment it is made,
    with a message, rather than to be the thing standing between the user and a
    recursion.

    Deliberately narrow: loopback host AND our own port. A second dubIS on
    another loopback port is a perfectly good source (that is the bench/shop
    case on one machine), so host alone must not disqualify a URL.
    """
    port = _own_port(api)
    if port is None:
        return
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    is_loopback = host in {"localhost", "::1"}
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_loopback = False
    if is_loopback and (parsed.port or (443 if parsed.scheme == "https" else 80)) == port:
        raise SourceConfigError(
            f"{url} is this server — a hub cannot be its own source; every merged "
            "read would ask itself for its own inventory"
        )


def _persist(
    api: Any, sources: tuple[Source, ...], default: str, *, write_default: bool,
) -> Registry:
    """Write the roster back, and — only when asked — the two default keys.

    Read-modify-write of the whole preferences dict, so every unrelated key the
    frontend owns survives.

    `write_default` is False for roster CRUD, for two reasons. Adding or renaming
    a source does not change anybody's default, so it has no business writing
    that decision at all; and when the default came from a `DUBIS_URL` boot
    value, writing it would persist a one-off override that `app_restart.py`
    deliberately strips from a relaunch — the override would outlive the session
    it was meant for.
    """
    registry = Registry(sources=sources, default=default)
    prefs = dict(api.load_preferences() or {})
    prefs[ROSTER_KEY] = [_source_to_entry(s) for s in registry.remotes]
    if write_default:
        prefs[ACTIVE_KEY] = registry.default
        prefs[SERVER_URL_KEY] = registry.default_url
    api.save_preferences(prefs)

    # The credentials go to their own file, keyed by the roster ids just
    # written. Rebuilt from the registry rather than patched, so removing a
    # source removes its token in the same breath — a token left behind under a
    # dead id would be handed straight back to whoever next reuses that id.
    #
    # Synthetic sources are excluded along with the roster: their token is
    # `DUBIS_TOKEN`, held in process precisely so it cannot be persisted.
    data_dir = _data_dir(api)
    if data_dir:
        token_store.save_tokens(
            data_dir,
            {s.id: s.token for s in registry.remotes if s.token and not s.synthetic},
        )
    return registry


def add_source(
    api: Any, *, url: str, source_id: str = "", name: str = "",
    token: str = "", enabled: bool = True,
) -> tuple[Registry, Source]:
    with _write_lock:
        registry = load_registry_from_api(api)
        normalized = normalize_url(url)
        if not normalized:
            raise SourceConfigError(f"source URL must start with http:// or https:// (got {url!r})")
        _reject_self_reference(api, normalized)
        for existing in registry.sources:
            if existing.url and existing.url == normalized:
                raise SourceConfigError(f"{existing.name!r} already points at {normalized}")
        taken = {s.id for s in registry.sources}
        new_id = str(source_id or "").strip() or _id_from_url(normalized, taken)
        if new_id in RESERVED_IDS:
            raise SourceConfigError(f"{new_id!r} is a reserved source id")
        if SELECTOR_SEPARATOR in new_id:
            raise SourceConfigError(
                f"a source id may not contain {SELECTOR_SEPARATOR!r}: it is the selector "
                f"separator, so {new_id!r} would be read as the merge of "
                f"{split_selectors(new_id)} and the source actually named would never "
                "be consulted"
            )
        if new_id in taken:
            raise SourceConfigError(f"a source with id {new_id!r} already exists")
        source = Source(
            id=new_id,
            name=str(name or "").strip() or name_from_url(normalized),
            url=normalized,
            token=str(token or ""),
            enabled=bool(enabled),
        )
        registry = _persist(api, registry.sources + (source,), registry.default,
                            write_default=False)
    return registry, source


def update_source(
    api: Any, source_id: str, *, name: str | None = None, url: str | None = None,
    token: str | None = None, enabled: bool | None = None,
) -> tuple[Registry, Source]:
    with _write_lock:
        registry = load_registry_from_api(api)
        current = registry.require(source_id)
        if current.is_local:
            raise SourceConfigError("the local source cannot be edited")
        if current.synthetic:
            raise SourceConfigError(
                f"source {source_id!r} is not in the roster — it comes from "
                f"{SERVER_URL_KEY}/DUBIS_URL. Add it as a source before editing it."
            )
        changes: dict[str, Any] = {}
        if url is not None:
            normalized = normalize_url(url)
            if not normalized:
                raise SourceConfigError(
                    f"source URL must start with http:// or https:// (got {url!r})"
                )
            for other in registry.sources:
                if other.id != source_id and other.url and other.url == normalized:
                    raise SourceConfigError(f"{other.name!r} already points at {normalized}")
            changes["url"] = normalized
        if name is not None:
            changes["name"] = str(name).strip() or name_from_url(changes.get("url", current.url))
        if token is not None:
            changes["token"] = str(token)
        if enabled is not None:
            changes["enabled"] = bool(enabled)
        if "url" in changes:
            _reject_self_reference(api, changes["url"])
        updated = replace(current, **changes)
        sources = tuple(updated if s.id == source_id else s for s in registry.sources)
        # Editing the URL of the source that IS the default has to move
        # `server_url` with it. Leaving the old address there is not merely
        # stale: `load_registry` adopts a `server_url` that matches no roster
        # entry as a synthetic source, so the machine the user just moved away
        # from comes back as a second, probeable, writable tab — and
        # `tools/dubis-cli`, which reads `server_url`, is sent there too.
        moved_the_default = source_id in registry.default_selectors and "url" in changes
        registry = _persist(api, sources, registry.default, write_default=moved_the_default)
    return registry, updated


def remove_source(api: Any, source_id: str) -> Registry:
    """Delete a roster entry. Removing the DEFAULT source resets the default to `local`.

    Resetting rather than refusing is deliberate: the alternative leaves
    `server_url` naming a server the roster no longer has, and every headerless
    client would be sent there.

    A window currently reading that source is not moved by the default changing —
    it names its source on every request — but its next request answers 404
    `source_not_found`, which is the honest answer and is what lets the frontend
    fall back to a tab that still exists.
    """
    with _write_lock:
        registry = load_registry_from_api(api)
        source = registry.require(source_id)
        if source.is_local:
            raise SourceConfigError("the local source cannot be removed")
        if source.synthetic:
            raise SourceConfigError(
                f"source {source_id!r} is not in the roster — it comes from "
                f"{SERVER_URL_KEY}/DUBIS_URL. Change the default instead."
            )
        sources = tuple(s for s in registry.sources if s.id != source_id)
        was_the_default = source_id in registry.default_selectors
        default = LOCAL_ID if was_the_default else registry.default
        registry = _persist(api, sources, default, write_default=was_the_default)
    return registry


def set_default(api: Any, target: str) -> Registry:
    """Persist the DEFAULT source. Changes nothing any open window is showing.

    This writes `active_source` + `server_url` and stops. It does not — cannot —
    move a request that named its own source with `X-Dubis-Source`, which is
    every request a dubIS window makes. What it changes is where a *headerless*
    client lands: `tools/dubis-cli`, curl, and a page load that has not yet
    resolved its own source.

    The `DUBIS_URL` boot default still outranks what this writes, matching
    `remote_mode.resolve_remote_base_url`'s documented env > preference order: an
    env override is meant to win for the session it was given for.
    """
    with _write_lock:
        registry = load_registry_from_api(api)
        tokens = split_selectors(target)
        if not tokens:
            raise SourceConfigError("no source selector given")
        if MERGED_ID in tokens and tokens != [MERGED_ID]:
            raise SourceConfigError(
                f"{MERGED_ID!r} already means every source and cannot be combined with others"
            )
        if tokens != [MERGED_ID]:
            # Validated with the same grammar the header uses, so every tab the
            # strip can produce — including a group like `bench,shop` — is a
            # default this route stores rather than 404s on.
            for token in tokens:
                source = registry.require(token)
                if len(tokens) == 1 and not source.enabled:
                    raise SourceConfigError(
                        f"source {token!r} is disabled; enable it before making it the default"
                    )
        registry = _persist(
            api, registry.sources, SELECTOR_SEPARATOR.join(tokens), write_default=True,
        )
    return registry


# ── outbound HTTP ────────────────────────────────────────────────────────────


class SourceClients:
    """One pooled `httpx.AsyncClient` per (url, token), shared by proxy + fan-out.

    The source's bearer token lives in the client's *default* headers rather
    than being re-attached at each call site, so no outbound request to a source
    can accidentally be made without it. A source with no token sends none at
    all — which is the normal case for a server behind the tailscale operator
    proxy, where identity comes from the proxy, not from us.

    `transport` is the injection seam the tests use (`httpx.MockTransport`);
    production leaves it None and gets httpx's real transport.
    """

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._transport = transport
        self._timeout = timeout
        self._clients: dict[tuple[str, str], httpx.AsyncClient] = {}
        self._lock = threading.Lock()

    def get(self, source: Source) -> httpx.AsyncClient:
        if source.is_local or not source.url:
            raise SourceConfigError(
                f"source {source.id!r} has no URL — the local source is served in-process, "
                "never over HTTP to ourselves"
            )
        key = (source.url, source.token)
        with self._lock:
            client = self._clients.get(key)
            if client is None or client.is_closed:
                kwargs: dict[str, Any] = {
                    "base_url": source.url,
                    "timeout": self._timeout,
                    "follow_redirects": False,
                }
                if source.token:
                    kwargs["headers"] = {"Authorization": f"Bearer {source.token}"}
                if self._transport is not None:
                    kwargs["transport"] = self._transport
                client = httpx.AsyncClient(**kwargs)
                self._clients[key] = client
            return client

    async def aclose(self) -> None:
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            await client.aclose()


@dataclass(frozen=True)
class ProbeResult:
    """What one reachability probe learned. `auth` is the load-bearing half.

    `/v1/health` is exempt from `AuthMiddleware` on every dubIS server, which is
    what makes it a usable reachability check — and is also exactly why
    reachability alone is a **trap**. Point the app at a server running
    `DUBIS_AUTH_MODE=on` without a credential and the health probe answers a
    cheerful 200 while every real request 401s: a green dot on a server that
    cannot serve a single row. Indistinguishable, from the dot, from working.

    So the probe asks a second question the exempt route cannot answer — see
    `probe` — and reports it here:

      "ok"       — an authenticated route answered. This source will serve data.
      "required" — it answered 401 and we hold no token for this source. The
                   user has to supply one; nothing else will fix it.
      "rejected" — it answered 401 and we DID send a token. The token is wrong,
                   expired, or not in that server's `DUBIS_TOKENS`.
      "unknown"  — not reachable at all, or the auth leg itself failed. Never
                   guessed: an inconclusive probe must not claim a credential
                   is missing any more than it may claim one is fine.
    """

    reachable: bool
    detail: str = ""
    auth: str = "unknown"


AUTH_OK = "ok"
AUTH_REQUIRED = "required"
AUTH_REJECTED = "rejected"
AUTH_UNKNOWN = "unknown"

# The route the auth leg asks for. Requirements, all three load-bearing: present
# on every dubIS server, cheap, and NOT in `server/auth.py`'s EXEMPT_PATHS —
# the whole point is to ask something the middleware actually gates. `/v1/meta`
# is a constant-ish read of already-loaded section orders.
AUTH_PROBE_PATH = "/v1/meta"


async def probe(
    source: Source, clients: SourceClients, timeout: float = PROBE_TIMEOUT_SECONDS,
) -> ProbeResult:
    """Is *source* reachable from THIS hub, and will it accept our credential?

    Never raises: an unreachable source is the normal answer, not an error.

    `detail` is a short human-readable reason when it is not reachable, so a red
    dot in the tab strip can say *why* rather than just being red. Empty when it
    is. `auth` is documented on `ProbeResult`.

    Two requests, not one, and in this order: `/v1/health` first, because it is
    the only route that answers on a server whose auth we cannot satisfy, and it
    is what distinguishes "down" from "gated". Then the gated route, whose only
    interesting outcome is 401 — any other status means the middleware let us
    through, which is the question being asked. A non-401 failure (500, a
    timeout on the second leg) leaves `auth` at "unknown" rather than inventing
    an answer.

    The local source is trivially reachable and trivially authorized: this code
    is running inside it, so a hub that were down could not have answered the
    request that asked, and loopback is `local` identity by definition.
    """
    if source.is_local:
        return ProbeResult(reachable=True, detail="", auth=AUTH_OK)
    try:
        response = await clients.get(source).get("/v1/health", timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — unreachable is expected, never fatal
        logger.info("sources: probe of %s (%s) failed: %s", source.id, source.url, exc)
        return ProbeResult(reachable=False, detail=type(exc).__name__)
    if response.status_code != 200:
        return ProbeResult(reachable=False, detail=f"HTTP {response.status_code}")
    # A 200 is not enough. Anything can answer 200 on a URL — a captive portal,
    # an SSO page, an nginx default, a load-balancer health shim — and a green
    # dot next to a server that cannot serve inventory is worse than a red one,
    # because it is the signal the user checks to decide whether the totals can
    # be trusted. `/v1/health` on a real dubIS is a constant `{"ok": true}`
    # (server/routes/meta.py), so require exactly that.
    try:
        body = response.json()
    except ValueError:
        return ProbeResult(reachable=False, detail="not a dubIS server")
    # Exact equality, not `body.get("ok")`: `{"ok": true, "service": "..."}` is
    # the most common shape a load-balancer or k8s health shim answers with, so
    # a truthy-`ok` test hands the commonest impostor of all a green dot.
    # tests/python/server/test_health_cors.py pins dubIS's body to exactly
    # `{"ok": true}` and says growing a field there needs re-deciding — this is
    # one of the places that would have to be re-decided with it.
    if body != {"ok": True}:
        return ProbeResult(reachable=False, detail="not a dubIS server")
    return ProbeResult(reachable=True, detail="", auth=await _probe_auth(source, clients, timeout))


async def _probe_auth(source: Source, clients: SourceClients, timeout: float) -> str:
    """Will *source* let this hub past its `AuthMiddleware`? See `ProbeResult`.

    Only 401 is interesting. Every other status — 200, 403, 404, even 500 —
    means the middleware admitted us and something further in answered, which is
    precisely the question. Reading more into those would be guessing: a 404
    from an older dubIS without `/v1/meta` says nothing about auth, and turning
    that into "credential required" would put a "needs a token" warning on a
    server that needs nothing.
    """
    try:
        response = await clients.get(source).get(AUTH_PROBE_PATH, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — same contract as the health leg
        logger.info(
            "sources: auth probe of %s (%s) was inconclusive: %s",
            source.id, source.url, type(exc).__name__,
        )
        return AUTH_UNKNOWN
    if response.status_code != 401:
        return AUTH_OK
    # 401 with a token we actually sent means the token is wrong — a different
    # message and a different fix from having none, which is the whole reason
    # these are two states and not one "needs auth".
    return AUTH_REJECTED if source.token else AUTH_REQUIRED
