"""Inventory mirror enable/disable/status — orchestrates installer + tailscale + prefs."""

import json
import logging
import os
import secrets
import sys
from typing import Any

from csv_io import atomic_write_text
from mirror_install import base, tailscale

logger = logging.getLogger(__name__)

READ_PORT = 7893
PUSH_PORT = 7892


class MirrorFacade:
    def __init__(self, api) -> None:
        self._api = api

    def _token_file(self) -> str:
        return os.path.join(self._api.base_dir, "mirror_token")

    def _snapshot_file(self) -> str:
        return os.path.join(self._api.base_dir, "inventory_mirror.json")

    def _state_file(self) -> str:
        return os.path.join(self._api.base_dir, "mirror_state.json")

    def _read_state(self) -> dict:
        path = self._state_file()
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write_state(self, state: dict) -> None:
        path = self._state_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        atomic_write_text(path, json.dumps(state), encoding="utf-8")

    def _allowlist(self) -> list:
        return self._read_state().get("allowlist", [])

    def _ensure_token(self) -> str:
        path = self._token_file()
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
        token = secrets.token_urlsafe(32)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(token)
        return token

    def _config(self) -> base.MirrorConfig:
        import inventory_mirror as _im
        return base.MirrorConfig(
            push_port=PUSH_PORT, read_port=READ_PORT,
            token_file=self._token_file(), snapshot_file=self._snapshot_file(),
            allowlist=self._allowlist(),
            python_exe=sys.executable,
            daemon_script=os.path.abspath(_im.__file__),
        )

    def enable_inventory_mirror(self) -> dict[str, Any]:
        self._ensure_token()
        # Seed the allowlist with the machine's own Tailscale login if not already set.
        state = self._read_state()
        if not state.get("allowlist"):
            login = tailscale.self_login()
            if login:
                state["allowlist"] = [login]
                self._write_state(state)
        # Build config AFTER allowlist is seeded so installer.install bakes it in.
        installer = base.get_installer()
        installer.install(self._config())
        try:
            serve_url = tailscale.enable_serve(READ_PORT)  # raises RuntimeError with actionable msg
        except Exception:
            try:
                installer.uninstall()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Mirror enable rollback (uninstall) failed: %s", exc)
            raise
        state = self._read_state()
        state["enabled"] = True
        state["serve_url"] = serve_url
        self._write_state(state)
        return self.get_inventory_mirror_info()

    def disable_inventory_mirror(self) -> dict[str, Any]:
        tailscale.disable_serve()
        try:
            base.get_installer().uninstall()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Mirror uninstall failed: %s", exc)
        state = self._read_state()
        state["enabled"] = False
        self._write_state(state)
        return self.get_inventory_mirror_info()

    def get_inventory_mirror_info(self) -> dict[str, Any]:
        state = self._read_state()
        try:
            installer = base.get_installer()
            installed, running = installer.is_installed(), installer.is_running()
        except NotImplementedError:
            installed = running = False
        return {
            "enabled": bool(state.get("enabled", False)),
            "installed": installed,
            "running": running,
            "serve_url": state.get("serve_url", ""),
            "read_port": READ_PORT,
            "allowlist": state.get("allowlist", []),
        }
