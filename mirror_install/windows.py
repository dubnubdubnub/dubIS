import subprocess

from mirror_install.base import Installer, MirrorConfig

TASK_NAME = "dubIS-InventoryMirror"


class WindowsInstaller(Installer):
    """Registers a logon Scheduled Task running the mirror daemon."""

    def _daemon_command(self, cfg: MirrorConfig) -> str:
        allow = ",".join(cfg.allowlist)
        return (
            f'"{cfg.python_exe}" "{cfg.daemon_script}" '
            f'--token-file "{cfg.token_file}" '
            f'--snapshot-file "{cfg.snapshot_file}" '
            f'--push-port {cfg.push_port} --read-port {cfg.read_port} '
            f'--allowlist "{allow}"'
        )

    def install(self, cfg: MirrorConfig) -> None:
        cmd = [
            "schtasks", "/Create", "/TN", TASK_NAME,
            "/TR", self._daemon_command(cfg),
            "/SC", "ONLOGON", "/RL", "LIMITED", "/F",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"schtasks create failed: {res.stderr.strip()}")
        # Start it now so the user doesn't have to re-login.
        subprocess.run(["schtasks", "/Run", "/TN", TASK_NAME],
                       capture_output=True, text=True)

    def uninstall(self) -> None:
        res = subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
                             capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"schtasks delete failed: {res.stderr.strip()}")

    def is_installed(self) -> bool:
        res = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME],
                             capture_output=True, text=True)
        return res.returncode == 0

    def is_running(self) -> bool:
        res = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"],
                             capture_output=True, text=True)
        return res.returncode == 0 and "Running" in res.stdout
