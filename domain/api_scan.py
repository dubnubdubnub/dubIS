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

    def install_tesseract(self) -> dict[str, Any]:
        """Best-effort install of the Tesseract OCR engine via winget (Windows).

        Runs winget non-interactively (machine scope; Windows shows a UAC prompt
        the user approves). Returns {ok: bool, message: str, available: bool}.
        On success the engine is re-detected so OCR works without an app restart.
        Never raises; always returns the dict.
        """
        import shutil
        import subprocess

        import ocr_engine

        if ocr_engine.ensure_tesseract():
            return {"ok": True, "message": "Tesseract is already installed.", "available": True}
        if sys.platform != "win32":
            return {"ok": False, "message": ocr_engine.INSTALL_HINT, "available": False}
        if not shutil.which("winget"):
            return {"ok": False, "message": "winget not found. " + ocr_engine.INSTALL_HINT, "available": False}
        try:
            proc = subprocess.run(
                ["winget", "install", "-e", "--id", "UB-Mannheim.TesseractOCR",
                 "--accept-package-agreements", "--accept-source-agreements"],
                capture_output=True, text=True, timeout=600,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("Tesseract install failed to start: %s", exc)
            return {"ok": False,
                    "message": f"Install failed to start: {exc}. " + ocr_engine.INSTALL_HINT,
                    "available": False}
        available = ocr_engine.ensure_tesseract()
        if proc.returncode == 0 and available:
            return {"ok": True, "message": "Tesseract installed.", "available": True}
        tail = (proc.stderr or proc.stdout or "").strip()[-400:]
        logger.warning("winget Tesseract install exited %s: %s", proc.returncode, tail)
        return {"ok": False, "available": available,
                "message": f"winget exited {proc.returncode}. {tail}".strip() + " " + ocr_engine.INSTALL_HINT}

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
