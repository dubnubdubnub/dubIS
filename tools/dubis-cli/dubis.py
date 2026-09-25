#!/usr/bin/env python3
"""dubis — command-line access to the dubIS /v1 API.

    dubis serve                                    start the /v1 server
    dubis parts list
    dubis parts adjust C1234 --adj-type add --quantity 50
    dubis carts plan 3 --preset min
    dubis --server reality-labs search 100nF       read another configured source
    dubis schema --json                            the whole surface, machine-readable

Command dispatch is driven by the generated table in commands.py (see
scripts/gen-cli.py), so every /v1 route is reachable without hand-written
plumbing. Path params are positional; everything else is a flag.

Exit codes are distinct so a caller can tell the failure modes apart without
parsing stderr — an agent that retries a 4 (start a server) the way it retries
a 2 (fix your arguments) will loop forever:

    0  success
    2  bad usage (argparse's own convention, kept)
    3  the server rejected the request, or a precheck did
    4  no /v1 server found

`--server` / `DUBIS_SERVER` pick which configured server SOURCE the hub serves
the request from (`X-Dubis-Source`); `--source` is unrelated — it tags
mutations. An unknown server exits 3; it never falls back to the hub default.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
# _HERE for commands.py (this directory is hyphenated, so it can never be a
# package); the repo root so dubis_client is reached as `tools.dubis_client`.
# Importing it bare off tools/ instead would give a SECOND module object for
# the same files whenever a caller already imported tools.dubis_client — and
# `except PartNotFoundError` would then miss the other copy's class.
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_REPO_ROOT))

from commands import COMMANDS  # noqa: E402  (needs the sys.path above)
from curated import CURATED  # noqa: E402
from tools.dubis_client import (  # noqa: E402
    NoServerFoundError,
    PartNotFoundError,
    ServerSelectionError,
    V1Error,
    connect,
    default_data_dir,
    derive_part_key,
    find_part,
    precheck_adjust,
    resolve_canonical_key,
    select_server,
)

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_SERVER = 3
EXIT_NO_SERVER = 4

# Top-level commands that do not go through the generated table: two builtins
# plus the hand-written hot path in curated.py. Kept as one set because the
# generated resources must not collide with any of them — enforced in
# build_parser() and pinned by a test.
_BUILTIN = ("serve", "schema")
_RESERVED = frozenset(_BUILTIN) | frozenset(CURATED)


def _json_arg(raw: str) -> Any:
    """argparse type for array/object params, which arrive as JSON text."""
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected JSON: {exc}") from exc


def _bool_arg(raw: str) -> bool:
    """Explicit --flag true|false rather than store_true.

    A boolean body param has three meaningful states — true, false, and
    absent — and store_true collapses the last two, so `--x false` would send
    nothing instead of false.
    """
    lowered = raw.strip().lower()
    if lowered in ("true", "1", "yes"):
        return True
    if lowered in ("false", "0", "no"):
        return False
    raise argparse.ArgumentTypeError("expected true or false")


_TYPES = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": _bool_arg,
    "array": _json_arg,
    "object": _json_arg,
}


def _flag_name(param: str) -> str:
    return "--" + param.replace("_", "-")


def _add_command_args(parser: argparse.ArgumentParser, cmd: dict) -> None:
    params = cmd["params"]
    for name in cmd["pathParams"]:
        parser.add_argument(name, type=_TYPES.get(params.get(name, {}).get("type"), str))

    for name in cmd["queryParams"] + cmd["bodyParams"]:
        # `source` is deliberately NOT added per-command: the global --source
        # already means exactly this, and adding both would collide on the
        # same dest. _build_request feeds args.source into the body.
        if name in _GLOBAL_DESTS:
            if name not in _ROUTE_MAPPED_GLOBALS:
                # A route param spelled like a global flag would be swallowed
                # by the global and never sent. Fail generation-time loud.
                raise RuntimeError(
                    f"{cmd['resource']} {cmd['verb']}: route param {name!r} collides "
                    "with a global CLI flag"
                )
            continue
        spec = params.get(name, {"type": "string", "required": False})
        required = bool(spec.get("required", False))
        parser.add_argument(
            _flag_name(name),
            dest=name,
            type=_TYPES.get(spec.get("type"), str),
            required=required,
            default=None,
            help=spec.get("type", "string") + ("" if required else " (optional)"),
        )

    if cmd["rawBody"]:
        parser.add_argument(
            "--body", type=_json_arg, required=True,
            help="JSON request body (this route takes an opaque object)",
        )


def _global_parser() -> argparse.ArgumentParser:
    """The flags accepted both before and after the subcommand.

    Attached to the top level AND to every subparser via `parents`, so
    `dubis --dry-run parts adjust ...` and `dubis parts adjust ... --dry-run`
    both work; an agent should not have to remember which position is legal.

    Every default is SUPPRESS on purpose. A subparser inheriting a normal
    default re-applies it after the top-level parser already stored the user's
    value, so `dubis --source ci parts adjust ...` would silently revert to
    "cli". With SUPPRESS the attribute is simply absent unless given, and
    _apply_global_defaults fills it in once, afterwards.
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="compact single-line JSON (default is pretty-printed)")
    common.add_argument("--data-dir", default=argparse.SUPPRESS,
                        help="data directory to discover the server in (default: <repo>/data)")
    common.add_argument("--source", default=argparse.SUPPRESS,
                        help="tag mutations with this source (default: cli), so "
                             "`dubis adjustments rollback-source <source>` can undo them")
    common.add_argument("--server", default=argparse.SUPPRESS, metavar="ID|NAME",
                        help="serve this request from that configured server source "
                             "(a /v1/sources id or name, `local`, `merged`, or a comma "
                             "list); sends X-Dubis-Source. Overrides $DUBIS_SERVER. "
                             "Default: the hub's saved default source")
    common.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS,
                        help="on a writing command, print the request instead of sending it")
    return common


_GLOBAL_DEFAULTS = {"json": False, "data_dir": None, "source": "cli", "dry_run": False,
                    "server": None}
_GLOBAL_DESTS = frozenset(_GLOBAL_DEFAULTS)
# Globals that deliberately stand in for a same-named route param (see
# _add_command_args); any other collision is an error.
_ROUTE_MAPPED_GLOBALS = frozenset({"source"})

SERVER_ENV = "DUBIS_SERVER"


def _apply_global_defaults(args: argparse.Namespace) -> None:
    flag_server = getattr(args, "server", None)
    for dest, default in _GLOBAL_DEFAULTS.items():
        if not hasattr(args, dest):
            setattr(args, dest, default)
    # --server beats $DUBIS_SERVER; a blank env var counts as unset.
    args.server_via = None
    if flag_server is not None:
        args.server_via = "flag"
    else:
        env_server = (os.environ.get(SERVER_ENV) or "").strip()
        if env_server:
            args.server, args.server_via = env_server, "env"


def _connect(args: argparse.Namespace):
    """connect(), then pin the client to the requested server source, if any.

    Resolution happens against the live hub's roster before the command's own
    request, so an unknown name exits 3 without anything having been read from
    (or written to) the hub's default source.
    """
    client = connect(str(_REPO_ROOT), data_dir=args.data_dir)
    if args.server is not None:
        selection = select_server(client, args.server, args.server_via)
        if selection.is_view:
            print(
                f"note: {selection.selector!r} is a merged view; the hub merges only "
                "GET /v1/parts and serves every other read from local",
                file=sys.stderr,
            )
    return client


def build_parser() -> argparse.ArgumentParser:
    common = _global_parser()
    parser = argparse.ArgumentParser(
        prog="dubis", description=__doc__, parents=[common],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subs = parser.add_subparsers(dest="resource", metavar="<resource>")

    serve = subs.add_parser("serve", parents=[common],
                            help="start the /v1 server in the foreground")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default="7891")
    serve.add_argument("--uds", default=None,
                       help="bind a Unix socket at this path instead of a TCP port; "
                            "on Linux each caller is identified by their kernel uid "
                            "(see server/peercred.py)")

    subs.add_parser("schema", parents=[common],
                    help="dump every command and its params")

    for name, spec in CURATED.items():
        curated_parser = subs.add_parser(name, parents=[common], help=spec["help"])
        spec["add_args"](curated_parser)

    by_resource: dict[str, dict[str, dict]] = {}
    for cmd in COMMANDS.values():
        by_resource.setdefault(cmd["resource"], {})[cmd["verb"]] = cmd

    for resource in sorted(by_resource):
        if resource in _RESERVED:
            raise RuntimeError(
                f"generated resource {resource!r} collides with a top-level command"
            )
        res_parser = subs.add_parser(resource, help=f"{len(by_resource[resource])} commands")
        verb_subs = res_parser.add_subparsers(dest="verb", metavar="<verb>")
        for verb in sorted(by_resource[resource]):
            cmd = by_resource[resource][verb]
            verb_parser = verb_subs.add_parser(
                verb, parents=[common], help=f"{cmd['httpVerb']} {cmd['path']}",
            )
            _add_command_args(verb_parser, cmd)

    return parser


def _build_request(cmd: dict, args: argparse.Namespace, source: str) -> dict:
    """Turn parsed args into {verb, path, query, body} — no I/O, so --dry-run
    can print exactly what would have been sent."""
    path = cmd["path"]
    for name in cmd["pathParams"]:
        path = path.replace("{" + name + "}", str(getattr(args, name)))

    query = {
        name: getattr(args, name)
        for name in cmd["queryParams"]
        if getattr(args, name, None) is not None
    }

    if cmd["rawBody"]:
        body = args.body
    else:
        body = {
            name: getattr(args, name)
            for name in cmd["bodyParams"]
            if getattr(args, name, None) is not None
        }
        if "source" in cmd["bodyParams"] and body.get("source") is None:
            body["source"] = source
        body = body or None

    return {"verb": cmd["httpVerb"], "path": path, "query": query, "body": body}


# ── Curation hooks ───────────────────────────────────────────────────────────
#
# The generated table is route-shaped: it knows a path and its params, nothing
# about what /v1 does with them. These hooks re-add the two behaviours that
# made the retired MCP client safer than raw curl, and they run against a live
# client, so --dry-run (which never connects) does not apply them.
#
# Without them, `dubis parts adjust GHOST-999 --adj-type add --quantity 5`
# exits 0 having changed nothing: /v1's domain layer only materializes a row
# for adj_type "set", and silently no-ops add/remove on an unknown key.


def _rewrite_path(cmd: dict, request: dict, param: str, value: str) -> dict:
    request["path"] = cmd["path"].replace("{" + param + "}", str(value))
    return request


def _hook_adjust(client, args, cmd, request):
    canonical = precheck_adjust(client, args.part_key, args.adj_type)
    return _rewrite_path(cmd, request, "part_key", canonical)


def _hook_canonical_part_key(client, args, cmd, request):
    _, canonical = resolve_canonical_key(client, args.part_key)
    return _rewrite_path(cmd, request, "part_key", canonical)


def _hook_bom_consume(client, args, cmd, request):
    """Resolve every match's part_key before consuming.

    POST /v1/bom/consume is canonical-key-strict per match, so an alias PN in
    a BOM would decrement a key that names no real part.
    """
    body = request.get("body") or {}
    matches = body.get("matches")
    if isinstance(matches, list):
        resolved = []
        for match in matches:
            if isinstance(match, dict) and match.get("part_key"):
                item = find_part(client, match["part_key"])
                if item is None:
                    raise PartNotFoundError(match["part_key"])
                match = {**match, "part_key": derive_part_key(item)}
            resolved.append(match)
        body["matches"] = resolved
    return request


_PRECHECKS = {
    "parts adjust": _hook_adjust,
    "parts get-history": _hook_canonical_part_key,
    "parts get-price-summary": _hook_canonical_part_key,
    "bom consume": _hook_bom_consume,
}


def _emit(payload: Any, compact: bool) -> None:
    if compact:
        print(json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str))
    else:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _run_serve(args: argparse.Namespace) -> int:
    """Start the server explicitly, in the foreground.

    This replaces the implicit per-invocation spawn the retired MCP client
    did — see tools/dubis_client/v1client.py's module docstring for why a CLI
    must not spawn one itself.

    The --data-dir is ALWAYS passed explicitly, never left to `python -m
    server`'s own default of "." — that default is the repo root, one level
    above the `<repo>/data` that connect() probes, so `dubis serve` followed by
    any other command would exit 4 while the repo root quietly accumulated
    .v1_port and .dubis_lock files.
    """
    data_dir = args.data_dir or default_data_dir(str(_REPO_ROOT))
    cmd = [sys.executable, "-m", "server", "--data-dir", data_dir]
    if args.uds:
        # --host/--port must NOT be forwarded alongside --uds: `python -m
        # server` treats the combination as a usage error precisely so that a
        # socket request can never be silently served over TCP. Their argparse
        # defaults here are just this parser's defaults, not something the
        # user asked for.
        cmd += ["--uds", args.uds]
    else:
        cmd += ["--host", args.host, "--port", str(args.port)]
    return subprocess.call(cmd, cwd=str(_REPO_ROOT))


def _dispatch_curated(args: argparse.Namespace) -> int:
    """Run a hand-written hot-path command.

    Every curated command is a read, so --dry-run has no write to withhold —
    but it still declines to run, because "dry-run executed it anyway" is the
    more surprising of the two behaviours and matches what the generated
    read-only commands do.
    """
    if args.dry_run:
        print(f"note: {args.resource} is read-only; --dry-run has nothing to withhold",
              file=sys.stderr)
        _emit({"dry_run": True, "command": args.resource}, args.json)
        return EXIT_OK

    client = _connect(args)
    _emit(CURATED[args.resource]["run"](client, args), args.json)
    return EXIT_OK


def _dispatch(cmd: dict, args: argparse.Namespace) -> int:
    request = _build_request(cmd, args, args.source)

    if args.dry_run:
        if not cmd["writes"]:
            print(
                f"note: {cmd['resource']} {cmd['verb']} is {cmd['httpVerb']} "
                "(read-only); --dry-run has nothing to withhold",
                file=sys.stderr,
            )
        if args.server is not None:
            # Unresolved: --dry-run never contacts the hub, so it cannot look
            # the name up (or reject it).
            request = {**request, "server": args.server}
        _emit({"dry_run": True, **request}, args.json)
        return EXIT_OK

    client = _connect(args)

    hook = _PRECHECKS.get(f"{cmd['resource']} {cmd['verb']}")
    if hook is not None:
        request = hook(client, args, cmd, request)

    verb = request["verb"].lower()
    if verb in ("get", "delete"):
        result = getattr(client, verb)(request["path"], **request["query"])
    else:
        result = getattr(client, verb)(request["path"], request["body"])

    if cmd["unwrap"] and isinstance(result, dict) and cmd["unwrap"] in result:
        result = result[cmd["unwrap"]]
    _emit(result, args.json)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _apply_global_defaults(args)

    if args.resource is None:
        parser.print_help()
        return EXIT_USAGE
    if args.resource == "serve":
        if args.server_via == "flag":
            print("error: --server picks a source on a running hub; `serve` starts one "
                  "and has no source to pick", file=sys.stderr)
            return EXIT_USAGE
        return _run_serve(args)
    if args.resource == "schema":
        _emit(COMMANDS, args.json)
        return EXIT_OK

    if args.resource in CURATED:
        try:
            return _dispatch_curated(args)
        except NoServerFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_NO_SERVER
        except (V1Error, PartNotFoundError, ServerSelectionError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_SERVER

    verb = getattr(args, "verb", None)
    if verb is None:
        # argparse exits 2 itself on this path, printing the resource's verbs.
        parser.parse_args([args.resource, "--help"])
        return EXIT_USAGE

    cmd = COMMANDS[f"{args.resource} {verb}"]
    try:
        return _dispatch(cmd, args)
    except NoServerFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NO_SERVER
    except (V1Error, PartNotFoundError, ServerSelectionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_SERVER


if __name__ == "__main__":
    sys.exit(main())
