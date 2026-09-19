"""app_launch: the launch-sequence decisions app.pyw makes, minus the webview.

app.pyw imports `webview` and so can't be imported in a test process — the same
reason remote_mode.py and app_restart.py exist. These two decisions used to be
`if not self.is_remote:` branches inline in `Launcher.run()`, untestable and
therefore unpinned; they live in app_launch.py now so the new launch contract
(always local, always one origin) has a test that fails if someone reintroduces
a second one. See docs/plans/2026-09-19-multi-server-hub-design.md §"Boot
changes".
"""

from __future__ import annotations

import os

import pytest

import app_launch
from remote_mode import resolve_remote_base_url

# ── splash_url: one origin, always ──────────────────────────────────────────

def test_splash_url_points_at_the_local_port():
    assert app_launch.splash_url("C:/app/splash.html", 51234) == "C:/app/splash.html?port=51234"


def test_splash_url_has_no_remote_origin_shape():
    """The `?base=<remote url>` shape is gone on purpose: a window that only
    ever loads the local origin can't hit the pywebview second-origin
    navigation race (CLAUDE.md Traps). Reintroducing it must break a test, not
    just a comment."""
    url = app_launch.splash_url("/app/splash.html", 9000)
    assert "base=" not in url
    assert url.count("?") == 1


def test_splash_url_rejects_a_missing_port():
    """The local server always boots now, so "no port" is a boot bug. Fail
    loudly rather than opening a window on a splash that can never navigate."""
    with pytest.raises(ValueError):
        app_launch.splash_url("/app/splash.html", 0)
    with pytest.raises(ValueError):
        app_launch.splash_url("/app/splash.html", None)


# ── seed_initial_active_source: the hub's starting source ───────────────────

def test_no_url_starts_on_local_and_never_calls_the_seam():
    calls = []
    for url in (None, ""):
        assert app_launch.seed_initial_active_source(url, seeder=calls.append) == "local"
    assert calls == []


def test_a_resolved_url_is_handed_to_the_registry():
    calls = []
    result = app_launch.seed_initial_active_source(
        "https://dubis.example.ts.net", seeder=calls.append
    )
    assert result == "seeded"
    assert calls == ["https://dubis.example.ts.net"]


def test_a_missing_registry_is_reported_not_raised(monkeypatch):
    """server/sources.py is built in parallel with this module. Until it lands
    (and if it ever regresses), a DUBIS_URL launch must still come up on local
    data with a loud log line — not die on the boot thread, which would leave
    the user on a splash that times out with a much worse message."""
    monkeypatch.setattr(app_launch, "_load_seeder", lambda: None)
    assert app_launch.seed_initial_active_source("https://dubis.example.ts.net") == "unavailable"


def test_seam_name_is_the_one_app_pyw_documents():
    assert app_launch.ACTIVE_SOURCE_SEAM == "server.sources.seed_initial_active_source"


# ── the two halves together: precedence now feeds the seed, not a boot branch ─

@pytest.mark.parametrize(
    "env, prefs, expected_url",
    [
        ({"DUBIS_URL": "https://env.example.com"}, {"server_url": "https://prefs.example.com"},
         "https://env.example.com"),
        ({}, {"server_url": "https://prefs.example.com"}, "https://prefs.example.com"),
    ],
)
def test_resolved_url_seeds_the_active_source(env, prefs, expected_url):
    calls = []
    resolved = resolve_remote_base_url(env, prefs)
    assert app_launch.seed_initial_active_source(resolved, seeder=calls.append) == "seeded"
    assert calls == [expected_url]


def test_a_remote_url_still_launches_a_local_server_window():
    """The regression this whole change exists to prevent: a configured remote
    URL must NOT divert the window to that origin. It seeds a source; the window
    still opens on the local port."""
    resolved = resolve_remote_base_url({"DUBIS_URL": "https://dubis.example.ts.net"}, {})
    assert resolved == "https://dubis.example.ts.net"
    assert app_launch.splash_url("/app/splash.html", 7891) == "/app/splash.html?port=7891"


# ── second launch: attach to the running hub ────────────────────────────────
#
# The whole point of putting plan_launch/resolve_launch in app_launch.py is that
# app.pyw can't be imported here. These pin the decision; what app.pyw does with
# each verdict (skip the boot thread, skip PnP, skip the mirror, run ephemeral,
# tear down nothing) is a handful of `if self.attached` lines around them.

def test_free_data_dir_means_this_process_owns_the_hub():
    plan = app_launch.plan_launch(owner_pid=None, owner_port=None, owner_responds=False)
    assert plan == app_launch.LaunchPlan(app_launch.OWN)


def test_live_owner_that_answers_is_attached_to():
    """The capability this exists to restore: a second window opens against the
    hub that's already running instead of refusing to start."""
    plan = app_launch.plan_launch(owner_pid=4242, owner_port=51234, owner_responds=True)
    assert plan.mode == app_launch.ATTACH
    assert plan.port == 51234          # the window polls the OWNER's port
    assert plan.owner_pid == 4242
    assert plan.message is None        # nothing to apologise for


def test_owner_holding_the_lock_but_not_serving_is_broken_and_says_so():
    plan = app_launch.plan_launch(owner_pid=4242, owner_port=51234, owner_responds=False)
    assert plan.mode == app_launch.BROKEN
    assert "4242" in plan.message and "51234" in plan.message
    assert "wedged" in plan.message


def test_owner_without_a_published_port_is_broken_separately():
    """port=None is what acquire_lock writes before uvicorn binds, so this is
    the mid-boot state — a different sentence from "nothing is answering"."""
    plan = app_launch.plan_launch(owner_pid=4242, owner_port=None, owner_responds=False)
    assert plan.mode == app_launch.BROKEN
    assert "starting up" in plan.message
    assert plan.port is None


def test_health_url_is_loopback_only():
    assert app_launch.health_url(51234) == "http://127.0.0.1:51234/v1/health"


# ── resolve_launch: the retry that turns a double-click into an attach ──────

def _clock(values):
    it = iter(values)
    return lambda: next(it)


def test_resolve_launch_returns_own_immediately_when_nobody_holds_the_lock():
    calls = []
    plan = app_launch.resolve_launch(
        "/data",
        find_owner=lambda d: calls.append(d) or None,
        responds=lambda p: pytest.fail("must not health-probe when there is no owner"),
        sleep=lambda s: pytest.fail("must not wait when there is no owner"),
        now=_clock([0.0]),
    )
    assert plan.mode == app_launch.OWN
    assert calls == ["/data"]


def test_resolve_launch_attaches_without_waiting():
    plan = app_launch.resolve_launch(
        "/data",
        find_owner=lambda d: (99, 7891),
        responds=lambda p: True,
        sleep=lambda s: pytest.fail("an answering owner must not be waited on"),
        now=_clock([0.0, 0.0]),
    )
    assert plan.mode == app_launch.ATTACH
    assert plan.port == 7891


def test_resolve_launch_waits_out_an_owner_that_is_still_booting():
    """The common second launch: double-clicked twice, the first instance holds
    the lock but hasn't bound its port yet. Retrying makes that an attach."""
    owners = iter([(99, None), (99, None), (99, 7891)])
    sleeps = []
    plan = app_launch.resolve_launch(
        "/data",
        find_owner=lambda d: next(owners),
        responds=lambda p: True,
        sleep=sleeps.append,
        now=_clock([0.0, 0.1, 0.2, 0.3, 0.4, 0.5]),
        wait_seconds=3.0,
    )
    assert plan.mode == app_launch.ATTACH
    assert plan.port == 7891
    assert sleeps == [app_launch.ATTACH_POLL_SECONDS] * 2


def test_resolve_launch_gives_up_and_reports_broken_past_the_deadline():
    sleeps = []
    plan = app_launch.resolve_launch(
        "/data",
        find_owner=lambda d: (99, 7891),
        responds=lambda p: False,
        sleep=sleeps.append,
        now=_clock([0.0, 5.0]),      # start, then already past the deadline
        wait_seconds=3.0,
    )
    assert plan.mode == app_launch.BROKEN
    assert sleeps == []              # deadline already blown: no pointless wait


# ── owner-exit watch ────────────────────────────────────────────────────────

def test_owner_is_declared_gone_only_after_repeated_failures():
    assert not app_launch.owner_has_exited(0)
    assert not app_launch.owner_has_exited(app_launch.OWNER_FAILURES_BEFORE_GONE - 1)
    assert app_launch.owner_has_exited(app_launch.OWNER_FAILURES_BEFORE_GONE)


def test_owner_gone_message_tells_the_user_what_to_do():
    """Honest degradation, not a reconnect story — there is nothing to
    reconnect to. The one action that works is naming it."""
    assert "exited" in app_launch.OWNER_GONE_MESSAGE
    assert "open dubIS again" in app_launch.OWNER_GONE_MESSAGE


# ── the probe against the real lockfile ─────────────────────────────────────

def test_probe_reports_no_owner_for_a_free_data_dir(tmp_path):
    assert app_launch.find_data_dir_owner(str(tmp_path)) is None


def test_probe_reports_the_live_holder_with_its_port(tmp_path):
    """server/lockfile.py's byte-0-lock / byte-1-content split exists so a
    losing caller can read the winner's pid and port. This is that reader."""
    from server.lockfile import acquire_lock

    handle = acquire_lock(str(tmp_path))
    handle.update_port(51234)
    try:
        assert app_launch.find_data_dir_owner(str(tmp_path)) == (os.getpid(), 51234)
    finally:
        handle.release()


def test_probe_does_not_keep_the_lock_it_probed_with(tmp_path):
    """The probe asks a question; it must not answer it by claiming the lock —
    start_server acquires for real moments later and would fail against us."""
    from server.lockfile import acquire_lock

    assert app_launch.find_data_dir_owner(str(tmp_path)) is None
    assert app_launch.find_data_dir_owner(str(tmp_path)) is None  # still free
    acquire_lock(str(tmp_path)).release()                         # and acquirable


def test_probe_reports_an_owner_that_has_not_published_a_port_yet(tmp_path):
    """acquire_lock writes port=None until uvicorn binds — the state
    resolve_launch retries through rather than calling broken."""
    from server.lockfile import acquire_lock

    handle = acquire_lock(str(tmp_path))
    try:
        assert app_launch.find_data_dir_owner(str(tmp_path)) == (os.getpid(), None)
    finally:
        handle.release()
