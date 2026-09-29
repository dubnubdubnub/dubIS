"""dubIS's own Chromium window for DigiKey product pages, driven over CDP.

WHY IT EXISTS
On Windows the DigiKey scrape runs in a hidden pywebview window, which is
WebView2, i.e. Chromium. On macOS pywebview is WKWebView, and DigiKey's
Cloudflare challenge on product pages never clears in it, hidden or visible
(verified live, 2026-09-29). A normal Chromium window clears the same challenge
in a few seconds and the existing scrape script then reads the product, so the
macOS and Linux backend drives one of those instead. No DigiKey login is
needed to read a product page. A session pushed by the bridge extension is
injected when there is one.

WHY IT IS VISIBLE
A headless Chromium never clears the challenge. macOS clamps an off-screen
`--window-position` back onto the screen, and a minimised window did not clear
it either, so the window shows. It is launched on first use and then reused.

WHY ITS OWN PROFILE
Chrome 136+ (and Brave, verified) ignores `--remote-debugging-port` on the
default user-data dir: the browser starts and the port never opens. The switch
works only beside a non-default `--user-data-dir`, so dubIS keeps a profile of
its own under the data dir. Nobody signs in there by hand, so losing saved
passwords costs nothing; signing in happens in the user's real browser,
through the extension.

PORTS
Chrome writes `DevToolsActivePort` only for `--remote-debugging-port=0`, and a
browser launched on port 0 was consistently refused by Cloudflare in testing, so
dubIS picks a free port itself and records it in `PORT_FILE` inside the profile.
That file is how a later call (or a later dubIS run) finds the browser that is
already holding the profile: Chromium allows one process per user-data dir, so
launching a second one would just hand the URL to the first and exit.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import plistlib
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from dubis_errors import DistributorError

logger = logging.getLogger(__name__)

PROFILE_DIRNAME = "digikey_browser_profile"
PORT_FILE = "dubis-cdp-port"
# How long a freshly launched browser gets to open its debugging port.
LAUNCH_TIMEOUT_S = 15.0
# Upper bound on one scripted page visit, including the Cloudflare wait.
PAGE_TIMEOUT_S = 60.0

# Chromium-based browsers that speak CDP, as (bundle id, app name). Order is
# the fallback preference when the default browser is not one of them.
MAC_CHROMIUM_APPS: tuple[tuple[str, str], ...] = (
    ("com.google.chrome", "Google Chrome"),
    ("com.brave.browser", "Brave Browser"),
    ("com.microsoft.edgemac", "Microsoft Edge"),
    ("org.chromium.chromium", "Chromium"),
    ("com.vivaldi.vivaldi", "Vivaldi"),
)
_MAC_LAUNCHSERVICES_PLIST = os.path.expanduser(
    "~/Library/Preferences/com.apple.LaunchServices/"
    "com.apple.launchservices.secure.plist"
)


LINUX_CHROMIUM_COMMANDS = (
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "brave-browser", "microsoft-edge",
)


def find_browser_exe() -> str | None:
    """Resolve a Chromium-family browser executable, or None."""
    if sys.platform == "win32":
        return _windows_default_browser_exe()
    if sys.platform == "darwin":
        return mac_chromium_exe()
    import shutil
    for cmd in LINUX_CHROMIUM_COMMANDS:
        path = shutil.which(cmd)
        if path:
            return path
    return None


def _windows_default_browser_exe() -> str | None:
    """The default browser's exe, read from the Windows registry."""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice",
        ) as key:
            prog_id = winreg.QueryValueEx(key, "ProgId")[0]
        with winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT,
            rf"{prog_id}\shell\open\command",
        ) as key:
            cmd = winreg.QueryValueEx(key, "")[0]
        exe = cmd.split('"')[1] if cmd.startswith('"') else cmd.split()[0]
        return exe if os.path.exists(exe) else None
    except OSError:
        return None
    except ImportError as exc:
        # Only reachable on a Windows build without `winreg`: unexpected, so
        # it gets a warning rather than a debug line.
        logger.warning("winreg unavailable on %s: %s", sys.platform, exc)
        return None


def mac_default_http_bundle_id(plist_path: str = _MAC_LAUNCHSERVICES_PLIST) -> str | None:
    """The bundle id LaunchServices opens http(s) links with, lowercased."""
    try:
        with open(plist_path, "rb") as f:
            data = plistlib.load(f)
    except (OSError, plistlib.InvalidFileException) as exc:
        logger.debug("LaunchServices plist unreadable: %s", exc)
        return None
    for handler in data.get("LSHandlers", []):
        if handler.get("LSHandlerURLScheme") in ("http", "https"):
            role = handler.get("LSHandlerRoleAll")
            if role:
                return str(role).lower()
    return None


def _mac_app_executable(app_path: str) -> str | None:
    try:
        with open(os.path.join(app_path, "Contents", "Info.plist"), "rb") as f:
            name = plistlib.load(f).get("CFBundleExecutable")
    except (OSError, plistlib.InvalidFileException):
        return None
    if not name:
        return None
    exe = os.path.join(app_path, "Contents", "MacOS", name)
    return exe if os.access(exe, os.X_OK) else None


def mac_chromium_exe(
    default_bundle_id: str | None = None,
    app_dirs: tuple[str, ...] | None = None,
) -> str | None:
    """The default browser if it is Chromium-based, else the first one installed.

    Safari and Firefox cannot be driven over CDP, so a Mac whose default is one
    of them still gets a working login as long as some Chromium browser exists.
    """
    if default_bundle_id is None:
        default_bundle_id = mac_default_http_bundle_id()
    if app_dirs is None:
        app_dirs = ("/Applications", os.path.expanduser("~/Applications"))
    ordered = sorted(MAC_CHROMIUM_APPS, key=lambda app: app[0] != default_bundle_id)
    for bundle_id, name in ordered:
        for base in app_dirs:
            exe = _mac_app_executable(os.path.join(base, f"{name}.app"))
            if exe:
                if bundle_id != default_bundle_id:
                    logger.debug("DigiKey browser: default %r is not Chromium, using %s",
                                 default_bundle_id, name)
                return exe
    return None


def require_browser_exe() -> str:
    """`find_browser_exe`, or a `DistributorError` naming what to install."""
    exe = find_browser_exe()
    if not exe:
        raise DistributorError(
            "No Chromium-based browser found (Chrome, Brave, Edge, Chromium or "
            "Vivaldi); DigiKey product previews need one on this platform",
            provider="digikey",
        )
    return exe


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def devtools_alive(port: int, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def default_profile_dir(cookies_file: str | None) -> str:
    """Where the profile lives: beside the cookie file, i.e. in the data dir."""
    if cookies_file:
        return os.path.join(os.path.dirname(os.path.abspath(cookies_file)), PROFILE_DIRNAME)
    import tempfile
    return os.path.join(tempfile.gettempdir(), f"dubis-{PROFILE_DIRNAME}-{os.getpid()}")


class DigikeyBrowser:
    """One Chromium process on dubIS's DigiKey profile, found or launched on demand.

    Every Playwright call runs on one dedicated thread: the sync API is bound to
    the thread that started it, and `/v1` handlers run on whichever anyio
    worker thread is free.
    """

    def __init__(self, profile_dir: str) -> None:
        self.profile_dir = profile_dir
        self._proc: subprocess.Popen | None = None
        self._launch_lock = threading.Lock()
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._pw: Any = None
        self._browser: Any = None
        self._browser_port: int | None = None

    # ── process ──────────────────────────────────────────────────────────

    @property
    def _port_file(self) -> str:
        return os.path.join(self.profile_dir, PORT_FILE)

    def has_profile(self) -> bool:
        return os.path.isdir(self.profile_dir)

    def running_port(self) -> int | None:
        """The debugging port of the browser holding the profile, if it is up."""
        try:
            with open(self._port_file, encoding="utf-8") as f:
                port = int(f.read().strip())
        except (OSError, ValueError):
            return None
        return port if devtools_alive(port) else None

    def launch_args(self, exe: str, port: int) -> list[str]:
        return [
            exe,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
        ]

    def launch(self, url: str) -> int:
        """Start the browser on the profile and wait for its debugging port.

        Raises `DistributorError` when no browser can be found or the port
        never opens, because either one means the login cannot work and the
        caller has to say so.
        """
        exe = require_browser_exe()
        with self._launch_lock:
            os.makedirs(self.profile_dir, mode=0o700, exist_ok=True)
            port = free_port()
            args = self.launch_args(exe, port)
            logger.debug("DigiKey browser: launching %s on port %d", exe, port)
            proc = subprocess.Popen(
                args + [url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            deadline = time.monotonic() + LAUNCH_TIMEOUT_S
            while not devtools_alive(port, timeout=0.5):
                if proc.poll() is not None or time.monotonic() > deadline:
                    _terminate(proc)
                    raise DistributorError(
                        "The DigiKey browser did not open its debugging port. "
                        "If a browser window from an earlier dubIS login is still "
                        "open, close it and try again.",
                        provider="digikey",
                    )
                time.sleep(0.2)
            with open(self._port_file, "w", encoding="utf-8") as f:
                f.write(str(port))
            self._proc = proc
            return port

    def open_tab(self, port: int, url: str) -> None:
        """Open `url` in a new tab of the running browser."""
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/json/new?{urllib.parse.quote(url, safe='')}",
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            json.loads(r.read() or b"{}")

    def ensure(self) -> int:
        """The running browser's port, launching one on about:blank if needed."""
        return self.running_port() or self.launch("about:blank")

    # ── scripted page visits ─────────────────────────────────────────────

    def _run(self, fn: Callable[[], Any], timeout: float) -> Any:
        if self._executor is None:
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="digikey-cdp",
            )
        return self._executor.submit(fn).result(timeout=timeout)

    def _context(self, port: int) -> Any:
        """The profile's own context; runs on the Playwright thread."""
        if self._browser is not None and (
            self._browser_port != port or not self._browser.is_connected()
        ):
            self._browser = None
        if self._browser is None:
            if self._pw is None:
                from playwright.sync_api import sync_playwright
                self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            self._browser_port = port
        contexts = self._browser.contexts
        if not contexts:
            raise RuntimeError("DigiKey browser exposes no profile context")
        return contexts[0]

    def visit(self, url: str, script: str | None = None, *,
              cf_timeout_s: float = 25.0) -> dict[str, Any]:
        """Open `url` in a new tab, wait out Cloudflare, optionally evaluate `script`.

        Returns ``{"url", "title", "cleared", "value"}``. ``cleared`` is False
        when the challenge was still showing at `cf_timeout_s`; ``value`` is
        then None. The tab is always closed; the browser never is.
        """
        port = self.ensure()

        def work() -> dict[str, Any]:
            page = self._context(port).new_page()
            try:
                page.goto(url, wait_until="load", timeout=30_000)
                deadline = time.monotonic() + cf_timeout_s
                title = page.title()
                while (not title or "Just a moment" in title) and time.monotonic() < deadline:
                    page.wait_for_timeout(500)
                    title = page.title()
                cleared = bool(title) and "Just a moment" not in title
                value = None
                if cleared and script is not None:
                    page.wait_for_load_state("load")
                    page.wait_for_timeout(1000)
                    value = page.evaluate(script)
                return {"url": page.url, "title": title, "cleared": cleared, "value": value}
            finally:
                try:
                    page.close()
                except Exception as exc:  # noqa: BLE001 - closing a dead tab is not news
                    logger.debug("DigiKey browser: closing tab failed: %s", exc)

        try:
            return self._run(work, timeout=PAGE_TIMEOUT_S)
        except Exception:
            # Drop the connection so the next visit reattaches cleanly.
            self._browser = None
            raise

    def add_cookies(self, cookies: list[dict]) -> None:
        """Put *cookies* into the profile, launching the browser if needed."""
        port = self.ensure()
        converted = []
        for c in cookies:
            item = {
                "name": c["name"],
                "value": c["value"],
                "domain": c.get("domain") or ".digikey.com",
                "path": c.get("path") or "/",
                "secure": bool(c.get("secure")),
                "httpOnly": bool(c.get("httpOnly")),
            }
            if c.get("expires"):
                item["expires"] = float(c["expires"])
            converted.append(item)
        self._run(lambda: self._context(port).add_cookies(converted), timeout=15)

    def clear_cookies(self) -> None:
        """Clear every cookie in the profile, if its browser is running."""
        port = self.running_port()
        if port is None:
            return
        self._run(lambda: self._context(port).clear_cookies(), timeout=15)

    def close(self) -> None:
        """Stop the browser dubIS launched, if any, and drop the connection."""
        if self._proc is not None:
            _terminate(self._proc)
            self._proc = None
        self._browser = None
        try:
            os.remove(self._port_file)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("DigiKey browser: could not remove port file: %s", exc)


def _terminate(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    except OSError:
        pass
