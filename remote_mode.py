"""Which dubIS server's data the user wants — a small, webview-free module so
this logic is unit-testable without importing app.pyw (which pulls in `webview`
and crashes on import outside a real GUI environment; see
tests/python/test_remote_mode.py).

Resolution precedence, binding and unchanged since Phase 1c Task 7
(docs/plans/2026-07-16-phase1c-remote-deploy-design.md §7):

  1. ``DUBIS_URL`` env var, if non-empty.
  2. ``server_url`` key in preferences.json, if non-empty.
  3. Neither set -> ``None``.

Env wins over preferences so a one-off override (e.g. a shell launch for
testing against a deployed server) doesn't require editing the persisted
prefs file, and clearing the env var falls straight back to whatever's on
disk. `tools/dubis-cli` reads `DUBIS_URL` the same way, so the two agree on
what "my server" means.

**What the answer is FOR has changed** (multi-server hub,
docs/plans/2026-09-19-multi-server-hub-design.md). It used to be a launch
*mode*: a URL here meant "do not boot a local server; point the webview at that
origin instead", which is exactly what forced a restart to change servers.
It no longer decides anything about booting — the local /v1 server always boots
and always serves the window. The URL now seeds which *source* that hub starts
active on, and the user can change it at runtime. Hence "None" below means
"start on local data", not "local mode": there is no other mode left. See
`app_launch.seed_initial_active_source` for where the answer goes.
"""

from __future__ import annotations

from typing import Any, Mapping


def resolve_remote_base_url(
    env: Mapping[str, str], preferences: Mapping[str, Any] | None
) -> str | None:
    """Return the remote server base URL the hub should start active on, or
    None to start on local data.

    `env` and `preferences` are passed in explicitly (rather than read from
    os.environ / a file here) so this stays a pure function: trivially
    testable, and callers control exactly what "preferences" means (app.pyw
    passes api.load_preferences()'s result; tests pass a plain dict).
    """
    env_url = env.get("DUBIS_URL", "").strip()
    if env_url:
        return env_url

    if preferences:
        prefs_url = str(preferences.get("server_url") or "").strip()
        if prefs_url:
            return prefs_url

    return None
