"""Inventory mirror enable/disable/status — orchestrates installer + tailscale + prefs."""

import logging
import os
import secrets
import sys
from typing import Any

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

    def _allowlist(self) -> list:
        prefs = self._api.load_preferences()
        return prefs.get("inventoryMirror", {}).get("allowlist", [])

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
        prefs = self._api.load_preferences()
        mirror_prefs = prefs.setdefault("inventoryMirror", {})
        if not mirror_prefs.get("allowlist"):
            login = tailscale.self_login()
            if login:
                mirror_prefs["allowlist"] = [login]
                self._api.save_preferences(prefs)
        # Build config AFTER allowlist is seeded so installer.install bakes it in.
        installer = base.get_installer()
        installer.install(self._config())
        serve_url = tailscale.enable_serve(READ_PORT)  # raises RuntimeError with actionable msg
        prefs = self._api.load_preferences()
        prefs.setdefault("inventoryMirror", {})["enabled"] = True
        prefs["inventoryMirror"]["serve_url"] = serve_url
        self._api.save_preferences(prefs)
        return self.get_inventory_mirror_info()

    def disable_inventory_mirror(self) -> dict[str, Any]:
        tailscale.disable_serve()
        try:
            base.get_installer().uninstall()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Mirror uninstall failed: %s", exc)
        prefs = self._api.load_preferences()
        prefs.setdefault("inventoryMirror", {})["enabled"] = False
        self._api.save_preferences(prefs)
        return self.get_inventory_mirror_info()

    def get_inventory_mirror_info(self) -> dict[str, Any]:
        prefs = self._api.load_preferences()
        mirror = prefs.get("inventoryMirror", {})
        try:
            installer = base.get_installer()
            installed, running = installer.is_installed(), installer.is_running()
        except NotImplementedError:
            installed = running = False
        return {
            "enabled": bool(mirror.get("enabled", False)),
            "installed": installed,
            "running": running,
            "serve_url": mirror.get("serve_url", ""),
            "read_port": READ_PORT,
            "allowlist": mirror.get("allowlist", []),
        }
