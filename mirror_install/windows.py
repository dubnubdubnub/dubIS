import os
import subprocess
import tempfile
from xml.sax.saxutils import escape

from mirror_install.base import Installer, MirrorConfig

TASK_NAME = "dubIS-InventoryMirror"
# How often the watchdog time-trigger re-checks/relaunches the daemon.
WATCHDOG_INTERVAL = "PT2M"


class WindowsInstaller(Installer):
    """Registers a logon Scheduled Task running the mirror daemon.

    Registered from an XML definition (not a bare `schtasks /Create /TR`) so the
    task can express settings the basic command form cannot:
      - A logon trigger for a prompt start, PLUS a time trigger that repeats
        every WATCHDOG_INTERVAL: this is the self-heal. Task Scheduler's
        RestartOnFailure does NOT reliably restart an externally-killed/crashed
        process (and never applies to on-demand `/Run` starts), so instead the
        repeating trigger re-launches the daemon and MultipleInstancesPolicy
        =IgnoreNew makes each tick a no-op while it is already running. Net
        effect matches the macOS LaunchAgent KeepAlive / Linux systemd
        Restart=on-failure: a crashed daemon is back within WATCHDOG_INTERVAL.
      - ExecutionTimeLimit = unlimited: the schtasks default is 72h, which would
        silently kill a long-running daemon after 3 days.
    Passing the daemon args as a separate <Arguments> element also sidesteps the
    schtasks /TR quote-mangling that used to corrupt the --allowlist value.
    """

    def _arguments(self, cfg: MirrorConfig) -> str:
        allow = ",".join(cfg.allowlist)
        return (
            f'"{cfg.daemon_script}" '
            f'--token-file "{cfg.token_file}" '
            f'--snapshot-file "{cfg.snapshot_file}" '
            f'--push-port {cfg.push_port} --read-port {cfg.read_port} '
            f'--allowlist "{allow}"'
        )

    def _build_task_xml(self, cfg: MirrorConfig) -> str:
        command = escape(cfg.python_exe)
        arguments = escape(self._arguments(cfg))
        return (
            '<?xml version="1.0" encoding="UTF-16"?>\n'
            '<Task version="1.2" '
            'xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
            "  <RegistrationInfo>\n"
            "    <Description>dubIS inventory mirror daemon</Description>\n"
            "  </RegistrationInfo>\n"
            "  <Triggers>\n"
            "    <LogonTrigger>\n"
            "      <Enabled>true</Enabled>\n"
            "    </LogonTrigger>\n"
            "    <TimeTrigger>\n"
            "      <StartBoundary>2020-01-01T00:00:00</StartBoundary>\n"
            "      <Enabled>true</Enabled>\n"
            "      <Repetition>\n"
            f"        <Interval>{WATCHDOG_INTERVAL}</Interval>\n"
            "        <StopAtDurationEnd>false</StopAtDurationEnd>\n"
            "      </Repetition>\n"
            "    </TimeTrigger>\n"
            "  </Triggers>\n"
            "  <Principals>\n"
            '    <Principal id="Author">\n'
            "      <LogonType>InteractiveToken</LogonType>\n"
            "      <RunLevel>LeastPrivilege</RunLevel>\n"
            "    </Principal>\n"
            "  </Principals>\n"
            "  <Settings>\n"
            "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
            "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
            "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
            "    <AllowHardTerminate>true</AllowHardTerminate>\n"
            "    <StartWhenAvailable>true</StartWhenAvailable>\n"
            "    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>\n"
            "    <IdleSettings>\n"
            "      <StopOnIdleEnd>false</StopOnIdleEnd>\n"
            "      <RestartOnIdle>false</RestartOnIdle>\n"
            "    </IdleSettings>\n"
            "    <AllowStartOnDemand>true</AllowStartOnDemand>\n"
            "    <Enabled>true</Enabled>\n"
            "    <Hidden>false</Hidden>\n"
            "    <RunOnlyIfIdle>false</RunOnlyIfIdle>\n"
            "    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n"
            "  </Settings>\n"
            '  <Actions Context="Author">\n'
            "    <Exec>\n"
            f"      <Command>{command}</Command>\n"
            f"      <Arguments>{arguments}</Arguments>\n"
            "    </Exec>\n"
            "  </Actions>\n"
            "</Task>\n"
        )

    def install(self, cfg: MirrorConfig) -> None:
        xml = self._build_task_xml(cfg)
        fd, path = tempfile.mkstemp(suffix=".xml", prefix="dubis-mirror-task-")
        try:
            with os.fdopen(fd, "w", encoding="utf-16") as f:
                f.write(xml)
            res = subprocess.run(
                ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", path, "/F"],
                capture_output=True, text=True,
            )
            if res.returncode != 0:
                raise RuntimeError(f"schtasks create failed: {res.stderr.strip()}")
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
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
