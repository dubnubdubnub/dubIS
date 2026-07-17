"""remote_mode.resolve_remote_base_url: the mode-resolution logic behind
app.pyw's remote-server mode (Phase 1c Task 7,
docs/plans/2026-07-16-phase1c-remote-deploy-design.md §7).

app.pyw itself can't be imported in a test process (it imports `webview`,
which requires a real GUI environment) — this is exactly why the resolution
logic lives in the small, webview-free remote_mode.py module instead. app.pyw
just calls resolve_remote_base_url(os.environ, api.load_preferences()) and
branches on the result; that wiring is covered by inspection (see
app.pyw's is_remote branch) plus the manual verify pass in
.superpowers/sdd/task-7-report.md. The splash.html JS-side base-vs-port URL
construction is pure JS with no test seam of its own; it's covered by the
existing splash.html E2E/manual-verify pattern noted in the report, not a new
unit test here.
"""

from __future__ import annotations

from remote_mode import resolve_remote_base_url


def test_neither_set_returns_none():
    assert resolve_remote_base_url({}, {}) is None
    assert resolve_remote_base_url({}, None) is None


def test_env_var_selects_remote_mode():
    assert resolve_remote_base_url(
        {"DUBIS_URL": "https://dubis.example.tailnet.ts.net"}, {}
    ) == "https://dubis.example.tailnet.ts.net"


def test_preferences_server_url_selects_remote_mode():
    assert resolve_remote_base_url(
        {}, {"server_url": "https://dubis.example.tailnet.ts.net"}
    ) == "https://dubis.example.tailnet.ts.net"


def test_env_wins_over_preferences():
    result = resolve_remote_base_url(
        {"DUBIS_URL": "https://env.example.com"},
        {"server_url": "https://prefs.example.com"},
    )
    assert result == "https://env.example.com"


def test_empty_env_falls_through_to_preferences():
    assert resolve_remote_base_url(
        {"DUBIS_URL": ""}, {"server_url": "https://prefs.example.com"}
    ) == "https://prefs.example.com"


def test_whitespace_only_env_falls_through_to_preferences():
    assert resolve_remote_base_url(
        {"DUBIS_URL": "   "}, {"server_url": "https://prefs.example.com"}
    ) == "https://prefs.example.com"


def test_empty_preferences_server_url_returns_none():
    assert resolve_remote_base_url({}, {"server_url": ""}) is None
    assert resolve_remote_base_url({}, {"server_url": "   "}) is None


def test_missing_server_url_key_returns_none():
    assert resolve_remote_base_url({}, {"some_other_key": "x"}) is None


def test_non_string_server_url_is_tolerated():
    # preferences.json is user-editable JSON; a stray non-string value must
    # not crash resolution, just be treated as "not set" once stringified
    # and stripped fails to produce anything meaningful. (None is the one
    # case explicitly falsy and skipped before str() is even called.)
    assert resolve_remote_base_url({}, {"server_url": None}) is None
