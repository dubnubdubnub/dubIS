import os
import subprocess

from mirror_install.base import Installer, MirrorConfig

UNIT_NAME = "dubis-inventory-mirror.service"


class LinuxInstaller(Installer):
    """Registers a systemd --user service running the mirror daemon."""

    def _unit_path(self) -> str:
        return os.path.expanduser(f"~/.config/systemd/user/{UNIT_NAME}")

    def install(self, cfg: MirrorConfig) -> None:
        unit_path = self._unit_path()
        os.makedirs(os.path.dirname(unit_path), exist_ok=True)

        allowlist = ",".join(cfg.allowlist)
        unit_content = (
            "[Unit]\n"
            "Description=dubIS inventory mirror daemon\n"
            "[Service]\n"
            f"ExecStart={cfg.python_exe} {cfg.daemon_script}"
            f" --token-file {cfg.token_file}"
            f" --snapshot-file {cfg.snapshot_file}"
            f" --push-port {cfg.push_port}"
            f" --read-port {cfg.read_port}"
            f" --allowlist {allowlist}\n"
            "Restart=on-failure\n"
            "[Install]\n"
            "WantedBy=default.target\n"
        )
        with open(unit_path, "w") as f:
            f.write(unit_content)

        subprocess.run(["systemctl", "--user", "daemon-reload"],
                       capture_output=True, text=True)

        res = subprocess.run(
            ["systemctl", "--user", "enable", "--now", UNIT_NAME],
            capture_output=True, text=True,
        )
        if res.returncode != 0:
            raise RuntimeError(f"systemctl enable failed: {res.stderr.strip()}")

    def uninstall(self) -> None:
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", UNIT_NAME],
            capture_output=True, text=True,
        )
        unit_path = self._unit_path()
        if os.path.exists(unit_path):
            os.remove(unit_path)
        subprocess.run(["systemctl", "--user", "daemon-reload"],
                       capture_output=True, text=True)

    def is_installed(self) -> bool:
        return os.path.exists(self._unit_path())

    def is_running(self) -> bool:
        res = subprocess.run(
            ["systemctl", "--user", "is-active", "--quiet", UNIT_NAME],
            capture_output=True, text=True,
        )
        return res.returncode == 0
