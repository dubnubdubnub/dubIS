"""Scan facade — source-file parsing, OCR, and phone-scan session management."""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)


class ScanFacade:
    def __init__(self, api) -> None:
        self._api = api

    def parse_source_file(self, path: str, template: str = "generic") -> list[dict[str, Any]]:
        """Parse a CSV/PDF/image source file into candidate line items.

        ``template`` selects a distributor profile ("generic"/"lcsc"/"digikey"/
        "mouser"/"pololu") for OCR/PDF extraction; defaults to "generic".
        """
        import mfg_direct_import
        return mfg_direct_import.parse_source_file(path, template)

    def parse_source_file_b64(
        self, file_b64: str, file_name: str, template: str = "generic",
    ) -> list[dict[str, Any]]:
        """Decode base64, write to temp file, parse, and return rows.

        ``template`` selects a distributor profile; defaults to "generic" for
        backward compatibility.
        """
        import base64
        import tempfile

        import mfg_direct_import
        ext = os.path.splitext(file_name)[1].lower()
        data = base64.b64decode(file_b64)
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tf:
            tf.write(data)
            tmp_path = tf.name
        try:
            return mfg_direct_import.parse_source_file(tmp_path, template)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def ocr_overlay_b64(
        self, file_b64: str, file_name: str, template: str = "generic",
    ) -> dict[str, Any]:
        """Decode base64, rasterize+OCR all pages, heuristic-prefill the grid.

        Returns {pages:[{image_b64,width,height,words,lines}], prefill_rows, template}.
        """
        import base64

        import ocr_layout

        ext = os.path.splitext(file_name)[1].lower()
        data = base64.b64decode(file_b64)
        return ocr_layout.extract_pages(data, ext, template)

    def ocr_engine_available(self) -> bool:
        """True if the Tesseract OCR binary can be located (PATH or common dirs)."""
        import ocr_engine
        return ocr_engine.ensure_tesseract()

    # ── Local picture/PDF reader ────────────────────────────────────────────
    #
    # Successor to the Windows-only `install_tesseract` this facade used to
    # carry (winget + a UAC prompt, and nothing at all on macOS/Linux). The
    # reader is cross-platform, downloads on demand, and is reached from the
    # pywebview *client shell* rather than /v1 — it installs and runs on the
    # client machine, and in remote-backend mode there is no local /v1 to carry
    # it. See `client_shell.ClientShell.start_reader_install` for the full
    # transport rationale and `reader_jobs.py` for the state machine.
    #
    # Every method below derives its target from `_reader_data_dir()` and never
    # accepts a path from the caller, so the preview, the install and the
    # uninstall cannot disagree about which directory they mean.

    def _reader_data_dir(self) -> str:
        """The data dir whose `reader/` subdirectory the local reader lives in.

        The single place the managed directory is chosen. `reader_install`
        derives (and safety-validates) `<data_dir>/reader` from it, and
        `reader_jobs` keys its single-flight lock on that derived path — so
        routing every reader call through here is what makes "one install per
        install target" mean one install per app.
        """
        return self._api.base_dir

    def start_reader_install(self) -> dict[str, Any]:
        """Begin (or join) a local reader install; return the initial status dict.

        Returns the same 14-key shape as `get_reader_install_status` rather than
        a bare job id, so the frontend has one dict to render from and does not
        have to special-case its first paint. `reader_jobs.start_install` is
        single-flight per managed directory: a second click while one install is
        in flight attaches to the running job instead of racing a second
        multi-GiB download onto the same disk.
        """
        import reader_jobs
        job_id = reader_jobs.start_install(self._reader_data_dir())
        return reader_jobs.get_status(job_id)

    def get_reader_install_status(self, job_id: str) -> dict[str, Any]:
        """Poll one install job. See `reader_jobs.InstallJob.status` for the contract.

        An unknown/expired id answers with a terminal error dict rather than
        raising, so a poll timer stops instead of retrying forever.
        """
        import reader_jobs
        return reader_jobs.get_status(job_id)

    def uninstall_reader(self) -> dict[str, Any]:
        """Stop the local reader and delete its managed directory.

        Returns {path, existed, bytes_reclaimed, file_count, server_stopped,
        reaped_pid}. Idempotent — uninstalling nothing succeeds with 0 bytes.
        """
        import reader_jobs
        return reader_jobs.uninstall_reader(self._reader_data_dir())

    def get_reader_status(self) -> dict[str, Any]:
        """Everything the reader UI needs before the user clicks anything.

        `install_dir` for the label; `installed`/`bytes_total`/`file_count`/
        `entries` for the uninstall confirm — measured by
        `reader_install.plan_uninstall`, the *same* measurement the uninstall
        itself reports, so the confirm can never name a different directory or a
        different size than what gets deleted; `server_running`/`endpoint` for
        whether a reader is up right now; and `active_job_id` so a Preferences
        panel reopened mid-install re-attaches to the running job rather than
        showing an idle Install button over a live download.
        """
        import reader_jobs
        data_dir = self._reader_data_dir()
        plan = reader_jobs.plan_uninstall_reader(data_dir)
        return {
            "install_dir": plan["path"],
            "installed": plan["exists"],
            "bytes_total": plan["bytes_total"],
            "file_count": plan["file_count"],
            "entries": plan["entries"],
            "server_running": plan["server_running"],
            "endpoint": reader_jobs.running_endpoint(data_dir) or "",
            "active_job_id": reader_jobs.active_job_id(data_dir) or "",
        }

    def start_scan_session(self, template: str = "generic") -> dict[str, Any]:
        """Mint a phone-scan session and return connection details.

        Validates *template*, registers a session on the running PnP server
        (stored on this api as ``self._api._pnp_server`` by app.pyw), discovers the
        machine's LAN IPv4 addresses, and best-effort opens a Windows firewall
        rule for the port. Returns ``{session_id, template, port, urls}``.
        """
        import distributor_profiles
        import pnp_server

        valid = distributor_profiles.template_keys()
        if template not in valid:
            raise ValueError(
                f"Unknown template '{template}'. Valid: {', '.join(valid)}"
            )

        server = getattr(self._api, "_pnp_server", None)
        if server is None:
            raise RuntimeError(
                "Phone-scan server is not running; cannot start a scan session."
            )

        session_id = pnp_server.create_scan_session(server, template)
        port = server.server_address[1]

        ips = self._lan_ipv4_addresses()
        self._open_firewall_port(port)

        urls = [f"http://{ip}:{port}/scan?s={session_id}" for ip in ips]
        return {
            "session_id": session_id,
            "template": template,
            "port": port,
            "urls": urls,
        }

    def match_part(self, mpn: str, manufacturer: str = "") -> dict[str, Any]:
        """Match an MPN against existing parts. See mfg_direct_import.match_part."""
        import mfg_direct_import
        return mfg_direct_import.match_part(self._api._get_cache(), mpn, manufacturer)

    @staticmethod
    def _lan_ipv4_addresses() -> list[str]:
        """Best-effort enumeration of this machine's non-loopback IPv4 addresses.

        Combines the UDP-connect "primary interface" trick with hostname
        resolution, dedupes, and drops loopback (127.*) and link-local
        (169.254.*) addresses.
        """
        import socket

        found: list[str] = []

        def _keep(ip: str) -> bool:
            return bool(ip) and not ip.startswith("127.") and not ip.startswith("169.254.")

        # Primary outbound interface (no packets actually sent).
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            found.append(s.getsockname()[0])
        except OSError as exc:
            logger.warning("LAN IP discovery (UDP trick) failed: %s", exc)
        finally:
            s.close()

        # Additional interfaces via hostname resolution.
        try:
            hostname = socket.gethostname()
            for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
                found.append(info[4][0])
        except OSError as exc:
            logger.warning("LAN IP discovery (getaddrinfo) failed: %s", exc)

        # Dedupe preserving order, drop loopback/link-local.
        seen: set[str] = set()
        result: list[str] = []
        for ip in found:
            if _keep(ip) and ip not in seen:
                seen.add(ip)
                result.append(ip)
        return result

    @staticmethod
    def _open_firewall_port(port: int) -> None:
        """Best-effort Windows Firewall inbound rule for *port*. Never raises.

        The port is usually already reachable (OpenPnP shares it), so failure is
        logged and ignored.
        """
        if sys.platform != "win32":
            return
        import subprocess

        rule_name = f"dubIS phone scan {port}"
        try:
            subprocess.run(
                [
                    "netsh", "advfirewall", "firewall", "add", "rule",
                    f"name={rule_name}",
                    "dir=in", "action=allow", "protocol=TCP",
                    f"localport={port}",
                ],
                check=False,
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("Could not add firewall rule for port %d: %s", port, exc)
