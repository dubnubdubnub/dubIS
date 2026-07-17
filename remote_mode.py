"""Remote-server mode resolution — a small, webview-free module so this logic
is unit-testable without importing app.pyw (which pulls in `webview` and
crashes on import outside a real GUI environment; see
tests/python/test_remote_mode.py).

Phase 1c Task 7 (docs/plans/2026-07-16-phase1c-remote-deploy-design.md §7):
point the desktop client at an already-deployed dubis-server instead of
spawning one locally. Resolution precedence, binding:

  1. ``DUBIS_URL`` env var, if non-empty.
  2. ``server_url`` key in preferences.json, if non-empty.
  3. Neither set -> ``None`` (local mode; today's behavior, unchanged).

Env wins over preferences so a one-off override (e.g. a shell launch for
testing against a deployed server) doesn't require editing the persisted
prefs file, and clearing the env var falls straight back to whatever's on
disk.
"""

from __future__ import annotations

from typing import Any, Mapping


def resolve_remote_base_url(
    env: Mapping[str, str], preferences: Mapping[str, Any] | None
) -> str | None:
    """Return the remote server base URL to use, or None for local mode.

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
