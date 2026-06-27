"""Preferences facade — load/save preferences."""

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

