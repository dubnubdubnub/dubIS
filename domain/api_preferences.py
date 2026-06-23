"""Preferences facade — load/save preferences and poll API configuration."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import csv_io

logger = logging.getLogger(__name__)


class PreferencesFacade:
    def __init__(self, api) -> None:
        self._api = api

    def load_preferences(self) -> dict[str, Any]:
        """Read preferences.json and return its contents (empty dict if missing/corrupt)."""
        try:
            if os.path.exists(self._api.prefs_json):
                with open(self._api.prefs_json, encoding="utf-8") as f:
                    data = json.load(f)
                # Migrate saved distributor_filter sets: "other" → "direct"
                if isinstance(data, dict) and isinstance(data.get("distributor_filter"), list):
                    data["distributor_filter"] = [
                        "direct" if d == "other" else d for d in data["distributor_filter"]
                    ]
                return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load preferences: %s", exc)
        return {}

    def save_preferences(self, prefs_json: str | dict[str, Any]) -> None:
        """Write preferences JSON string to disk."""
        prefs = self._api._ensure_parsed(prefs_json)
        csv_io.atomic_write_text(
            self._api.prefs_json, json.dumps(prefs, indent=2), encoding="utf-8",
        )

    def get_poll_api_info(self) -> dict[str, Any]:
        """Return the local poll API URL and active port."""
        import poll_api
        server = getattr(self._api, "_poll_server", None)
        prefs = self._api.load_preferences()
        info: dict[str, Any] = {
            "default_port": poll_api.POLL_PORT,
            "configured_port": prefs.get("pollApiPort"),
            "running": server is not None,
        }
        if server is not None:
            host, port = server.server_address
            info["host"] = host
            info["port"] = port
            info["url"] = f"http://{host}:{port}"
        else:
            info["host"] = ""
            info["port"] = None
            info["url"] = ""
        return info

    def set_poll_api_port(self, port: int | str) -> dict[str, Any]:
        """Restart the poll API server on a new port and persist to preferences."""
        import poll_api
        try:
            port_int = int(port)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"port must be an integer, got {port!r}") from exc
        if port_int < 1024 or port_int > 65535:
            raise ValueError(f"port out of range (1024-65535): {port_int}")
        poll_api.restart_poll_server(self._api, port_int)
        prefs = self._api.load_preferences()
        prefs["pollApiPort"] = port_int
        self._api.save_preferences(prefs)
        return self._api.get_poll_api_info()
