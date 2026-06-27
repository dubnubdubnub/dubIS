import os
import plistlib
import subprocess

from mirror_install.base import Installer, MirrorConfig

LABEL = "com.dubis.inventory-mirror"


class MacOSInstaller(Installer):
    """Registers a launchd LaunchAgent running the mirror daemon."""

    def _plist_path(self) -> str:
        return os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")

    def install(self, cfg: MirrorConfig) -> None:
        plist_path = self._plist_path()
        os.makedirs(os.path.dirname(plist_path), exist_ok=True)

        payload = {
            "Label": LABEL,
            "ProgramArguments": [
                cfg.python_exe, cfg.daemon_script,
                "--token-file", cfg.token_file,
                "--snapshot-file", cfg.snapshot_file,
                "--push-port", str(cfg.push_port),
                "--read-port", str(cfg.read_port),
                "--allowlist", ",".join(cfg.allowlist),
            ],
            "RunAtLoad": True,
            "KeepAlive": True,
        }
        with open(plist_path, "wb") as f:
            plistlib.dump(payload, f)

        # Unload first (ignore errors — may not be loaded)
        subprocess.run(["launchctl", "unload", plist_path],
                       capture_output=True, text=True)

        res = subprocess.run(["launchctl", "load", "-w", plist_path],
                             capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"launchctl load failed: {res.stderr.strip()}")

    def uninstall(self) -> None:
        plist_path = self._plist_path()
        subprocess.run(["launchctl", "unload", "-w", plist_path],
                       capture_output=True, text=True)
        if os.path.exists(plist_path):
            os.remove(plist_path)

    def is_installed(self) -> bool:
        return os.path.exists(self._plist_path())

    def is_running(self) -> bool:
        res = subprocess.run(["launchctl", "list"],
                             capture_output=True, text=True)
        return res.returncode == 0 and LABEL in res.stdout
