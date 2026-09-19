"""The launch-sequence decisions app.pyw used to make inline, kept webview-free
so they are unit-testable.

Same reasoning as `remote_mode.py` and `app_restart.py`: `app.pyw` imports
`webview`, which needs a real GUI environment and so cannot be imported in a
test process — anything worth testing lives beside it rather than in it.

What changed here (multi-server hub,
docs/plans/2026-09-19-multi-server-hub-design.md): the desktop app used to make
a *mode* decision at launch. A resolved remote URL meant "don't boot a local
server at all; point the webview at that origin instead"
(`splash.html?base=<url>`), and that single decision is what forced a restart to
change servers. There is no such mode any more:

  * the local /v1 server ALWAYS boots — it is the hub,
  * the window ALWAYS opens on `splash.html?port=<local port>`,
  * a resolved remote URL only seeds which *source* the hub starts active on,
    which the user can then change at runtime without a restart.

That is why `splash_url()` below takes a port and nothing else. The only origin
this window ever loads is the local one, which keeps the pywebview
second-origin navigation race (see CLAUDE.md's Traps and splash.html's header)
permanently out of reach rather than merely avoided by timing.
"""

from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)

# The seam the hub's source registry is expected to expose. `server/sources.py`
# is owned by the multi-server work in parallel with this module; app.pyw hands
# the resolved URL across this one named function and knows nothing else about
# the registry.
#
# Contract: `seed_initial_active_source(url: str) -> None`, called once, on the
# server-boot thread, BEFORE `server.run.start_server()` builds the app — so the
# registry created inside `create_app` can read the seed and come up with that
# source already active. Seeding is advisory: the roster/active-source state is
# the registry's, and anything the user changes at runtime supersedes it.
#
# The `DUBIS_URL` env var is what makes this seam load-bearing rather than
# decorative. The registry can derive an active source from preferences on its
# own (`server_url` is a persisted key it reads), but `DUBIS_URL` outranks
# preferences and is deliberately never written to them — `app_restart.py`
# strips it from a relaunch precisely so a one-off override can't outlive the
# session. Without this call, an env-launched session would silently come up on
# the wrong server's data.
SEAM_MODULE = "server.sources"
SEAM_FUNCTION = "seed_initial_active_source"
ACTIVE_SOURCE_SEAM = f"{SEAM_MODULE}.{SEAM_FUNCTION}"


def splash_url(splash_path: str, port: int) -> str:
    """The URL the window is created on.

    Always the local splash pointed at the local /v1 port. There is deliberately
    no second shape (the old `?base=<remote url>`): the window is served from
    the hub and never navigates to another origin.
    """
    if not port:
        raise ValueError(
            f"splash_url needs the bound local /v1 port; got {port!r}. The local "
            "server always boots now, so a missing port is a boot bug, not a mode."
        )
    return f"{splash_path}?port={port}"


def _load_seeder() -> Callable[[str], None] | None:
    """Resolve `ACTIVE_SOURCE_SEAM`, or None if it isn't there yet."""
    try:
        module = __import__(SEAM_MODULE, fromlist=[SEAM_FUNCTION])
    except ImportError:
        return None
    return getattr(module, SEAM_FUNCTION, None)


def seed_initial_active_source(url: str | None, seeder=None) -> str:
    """Tell the hub which source to start active on. Returns what happened:

      "local"       — no URL resolved (neither DUBIS_URL nor preferences'
                      server_url is set), so the hub starts on its own data.
                      This is the common case and not worth logging.
      "seeded"      — the URL was handed to the registry.
      "unavailable" — a URL was resolved but the registry seam does not exist.
                      The caller should log this loudly: the user asked for a
                      specific server and did not get it. It is deliberately not
                      fatal — the hub is fully usable on local data, and dying
                      on the boot thread would leave the user staring at a
                      splash that times out with a worse message.

    `seeder` is injectable so this is testable without `server/sources.py`
    existing (it is built in parallel with this module).
    """
    if not url:
        return "local"
    if seeder is None:
        seeder = _load_seeder()
    if seeder is None:
        return "unavailable"
    seeder(url)
    return "seeded"


# ── second launch: attach to the running hub instead of dying ────────────────
#
# The data-dir lock guards exactly one thing: a single writer to the local
# SQLite + CSVs. It was never meant to guard *windows*. A second window doesn't
# need to own the data dir — it needs a server to talk to, and when the lock is
# held there is one, already running, a few hundred microseconds away.
#
# `server/lockfile.py` was built for this. Its module docstring (lines 14-21)
# spells out that the lock byte and the JSON content live at different offsets
# "specifically so a losing caller can still read the winner's pid/port" — byte
# 0 holds the lock, the content starts at byte 1 and is never locked. So the
# discovery channel is deliberate and pre-existing; this is the first caller to
# use it for anything but an error message.
#
# Why the probe is `acquire_lock` itself rather than a read of the file: the
# file's content is only meaningful when someone actually holds the lock. A
# crashed process leaves its pid/port behind (the OS drops the lock but not the
# bytes), and a port from a dead run may since have been taken by an unrelated
# process. Asking the OS "is this held right now" is the only authoritative
# question, and `acquire_lock` already answers it — raising with the live
# holder's pid/port, or succeeding when the holder is gone. The probe releases
# immediately; `server.run.start_server` re-acquires for real a moment later.
# The gap between those two is a genuine (millisecond) race that two launches
# fired at the same instant can lose, and `app.pyw`'s DataDirLockedError handler
# is the backstop for it.

# How long to keep re-probing an owner that holds the lock but has not published
# a usable port yet. The owner writes port=None at acquire time and fills it in
# once uvicorn binds (server/run.py's _write_port_file_when_started), so a
# double-click-twice launch routinely arrives inside that window — the most
# common second launch there is. Retrying turns it into an attach instead of an
# error, and the wait is paid only when an owner genuinely exists.
ATTACH_WAIT_SECONDS = 3.0
ATTACH_POLL_SECONDS = 0.1

# Health-probe timeout for "is the owner actually serving". Loopback: a live
# server answers in single-digit ms and a dead port refuses the connection
# immediately, so this bound is only reached by a wedged process.
HEALTH_TIMEOUT_SECONDS = 0.5

# Owner-liveness watch, attached windows only. The owner is already up when we
# attach, so a failure here means it exited or wedged; three strikes at 5s keeps
# a transient blip from declaring a false death.
OWNER_POLL_SECONDS = 5.0
OWNER_FAILURES_BEFORE_GONE = 3

OWN = "own"          # nothing holds the lock: boot the hub, this window owns it
ATTACH = "attach"    # a live hub owns the data dir: serve this window from it
BROKEN = "broken"    # the lock is held but no one is serving: say so plainly

OWNER_GONE_TITLE = "dubIS has closed"
OWNER_GONE_MESSAGE = (
    "The dubIS instance that was serving this window has exited.\n\n"
    "This window is no longer connected to a server, so it can't load or save "
    "anything. Close it and open dubIS again."
)


class LaunchPlan:
    """What this launch should do: `mode`, the `port` the window's splash should
    poll (None when the hub is still to be booted on a port not yet chosen), the
    owner's pid when there is one, and the dialog text for BROKEN.

    A plain value object — comparing two plans compares their fields, which is
    what the tests assert on.
    """

    __slots__ = ("mode", "port", "owner_pid", "message")

    def __init__(self, mode: str, port: int | None = None,
                 owner_pid: int | None = None, message: str | None = None):
        self.mode = mode
        self.port = port
        self.owner_pid = owner_pid
        self.message = message

    def __eq__(self, other):
        if not isinstance(other, LaunchPlan):
            return NotImplemented
        return (self.mode, self.port, self.owner_pid, self.message) == (
            other.mode, other.port, other.owner_pid, other.message)

    def __repr__(self):
        return (f"LaunchPlan(mode={self.mode!r}, port={self.port!r}, "
                f"owner_pid={self.owner_pid!r}, message={self.message!r})")


def health_url(port: int) -> str:
    """The hub's health endpoint. Loopback only — the hub is always local."""
    return f"http://127.0.0.1:{port}/v1/health"


def plan_launch(owner_pid: int | None, owner_port: int | None,
                owner_responds: bool) -> LaunchPlan:
    """The whole second-launch decision, as a pure function.

    `owner_pid is None` means nothing holds the data-dir lock. Otherwise a live
    process holds it, and the only question is whether it is actually serving:
    a hub that answers /v1/health is one this window can be served from, and a
    hub that doesn't is a genuinely broken state the user has to be told about
    (wedged mid-shutdown, or a process holding the dir without a server).
    """
    if owner_pid is None:
        return LaunchPlan(OWN)

    if not owner_port:
        return LaunchPlan(
            BROKEN, owner_pid=owner_pid,
            message=(
                f"Another dubIS process (pid={owner_pid}) is using this data "
                f"directory but hasn't published a port yet.\n\n"
                f"It's either still starting up or it failed during startup. "
                f"Wait a moment and open dubIS again; if this keeps happening, "
                f"close that process first."
            ),
        )

    if not owner_responds:
        return LaunchPlan(
            BROKEN, port=owner_port, owner_pid=owner_pid,
            message=(
                f"Another dubIS process (pid={owner_pid}) is using this data "
                f"directory, but nothing is answering on its port "
                f"({owner_port}).\n\n"
                f"That instance is wedged or part-way through shutting down. "
                f"Close it (end process {owner_pid}) and open dubIS again."
            ),
        )

    return LaunchPlan(ATTACH, port=owner_port, owner_pid=owner_pid)


def owner_has_exited(consecutive_failures: int) -> bool:
    """Whether an attached window should declare its hub gone. See
    OWNER_FAILURES_BEFORE_GONE."""
    return consecutive_failures >= OWNER_FAILURES_BEFORE_GONE


def hub_responds(port: int, timeout: float = HEALTH_TIMEOUT_SECONDS) -> bool:
    """True if something answers /v1/health on this loopback port.

    Any HTTP answer counts, including a 4xx/5xx: the question is "is a server
    there", not "is it happy". Only a transport failure (connection refused,
    timeout) means no.
    """
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(health_url(port), timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def find_data_dir_owner(data_dir: str):
    """(pid, port) of the live process holding `data_dir`'s lock, or None.

    Probes by acquiring the lock and releasing it again — see this section's
    header for why that, and not a read of the lock file, is the authoritative
    question. Releasing immediately is the point: this is a question, not a
    claim. `server.run.start_server` makes the real claim moments later.

    A non-contention failure (permission denied, read-only data dir) is NOT
    swallowed into "no owner": it propagates, because booting a hub that can't
    lock its own data dir is exactly the silent double-writer this lock exists
    to prevent.
    """
    from dubis_errors import DataDirLockedError
    from server.lockfile import acquire_lock  # stdlib-only module; cheap import

    try:
        handle = acquire_lock(data_dir)
    except DataDirLockedError as exc:
        return (exc.pid, exc.port)
    handle.release()
    return None


def resolve_launch(data_dir: str, *, find_owner=None, responds=None,
                   sleep=None, now=None,
                   wait_seconds: float = ATTACH_WAIT_SECONDS) -> LaunchPlan:
    """`plan_launch` against the real world, retrying a BROKEN verdict until
    `wait_seconds` is up — an owner that is mid-boot becomes an attach rather
    than an error (see ATTACH_WAIT_SECONDS).

    Every dependency is injected so the loop itself is testable: no sockets, no
    clock, no lock file.
    """
    import time as _time

    find_owner = find_owner or find_data_dir_owner
    responds = responds or hub_responds
    sleep = sleep or _time.sleep
    now = now or _time.monotonic

    deadline = now() + wait_seconds
    while True:
        owner = find_owner(data_dir)
        if owner is None:
            return LaunchPlan(OWN)
        pid, port = owner
        plan = plan_launch(pid, port, responds(port) if port else False)
        if plan.mode != BROKEN or now() >= deadline:
            return plan
        sleep(ATTACH_POLL_SECONDS)
