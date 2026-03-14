# OpenPnP HTTP Bridge — Startup Script
# Deploys to: C:\Users\isaac\.openpnp2\scripts\Startup.py
#
# Jython 2.7 — runs inside the OpenPnP JVM. Starts an HTTP server on the
# Tailscale interface exposing live machine state, camera frames, parts,
# feeders, packages, and job info.
#
# Available globals: machine, config, gui, scripting

from com.sun.net.httpserver import HttpServer, HttpHandler
from java.net import InetSocketAddress
from java.io import ByteArrayOutputStream
from javax.imageio import ImageIO
import json
import traceback

BIND_IP = "100.96.249.60"  # Tailscale only
PORT = 8899


def _send_json(exchange, status, data):
    """Send a JSON response."""
    body = json.dumps(data)
    exchange.getResponseHeaders().set("Content-Type", "application/json")
    exchange.sendResponseHeaders(status, len(body))
    out = exchange.getResponseBody()
    out.write(bytearray(body, "utf-8"))
    out.close()


def _send_error(exchange, status, msg):
    """Send a JSON error response."""
    _send_json(exchange, status, {"ok": False, "error": msg})


def _get_location_dict(location):
    """Convert an OpenPnP Location to a dict."""
    return {
        "x": location.getX(),
        "y": location.getY(),
        "z": location.getZ(),
        "rotation": location.getRotation(),
        "units": str(location.getUnits()),
    }


# ── Handlers ─────────────────────────────────────────────────


class HealthHandler(HttpHandler):
    def handle(self, exchange):
        try:
            _send_json(exchange, 200, {"ok": True})
        except Exception:
            traceback.print_exc()


class StateHandler(HttpHandler):
    def handle(self, exchange):
        try:
            heads = []
            for head in machine.getHeads():
                nozzles = []
                for nozzle in head.getNozzles():
                    part = nozzle.getPart()
                    nozzles.append({
                        "id": nozzle.getId(),
                        "name": nozzle.getName(),
                        "part": part.getId() if part else None,
                        "part_on": nozzle.isPartOn() if hasattr(nozzle, "isPartOn") else None,
                    })
                heads.append({
                    "id": head.getId(),
                    "name": head.getName(),
                    "nozzles": nozzles,
                })

            # Job processor state
            job_processor = machine.getPnpJobProcessor()
            job_running = False
            if job_processor:
                try:
                    job_running = job_processor.isRunning()
                except Exception:
                    pass

            state = {
                "ok": True,
                "homed": machine.isHomed(),
                "enabled": machine.isEnabled(),
                "heads": heads,
                "job_running": job_running,
            }
            _send_json(exchange, 200, state)
        except Exception as e:
            traceback.print_exc()
            _send_error(exchange, 500, str(e))


class PartsHandler(HttpHandler):
    def handle(self, exchange):
        try:
            parts = []
            for part in config.getParts():
                pkg = part.getPackage()
                parts.append({
                    "id": part.getId(),
                    "name": part.getName(),
                    "description": part.getDescription() if hasattr(part, "getDescription") else None,
                    "package": pkg.getId() if pkg else None,
                    "height": part.getHeight().getValue() if part.getHeight() else None,
                    "speed": part.getSpeed(),
                })
            _send_json(exchange, 200, {"ok": True, "parts": parts})
        except Exception as e:
            traceback.print_exc()
            _send_error(exchange, 500, str(e))


class FeedersHandler(HttpHandler):
    def handle(self, exchange):
        try:
            feeders = []
            for f in machine.getFeeders():
                part = f.getPart()
                loc = f.getLocation()
                feeders.append({
                    "id": f.getId(),
                    "name": f.getName(),
                    "part_id": part.getId() if part else None,
                    "enabled": f.isEnabled(),
                    "feed_count": f.getFeedCount() if hasattr(f, "getFeedCount") else None,
                    "location": _get_location_dict(loc) if loc else None,
                    "type": type(f).__name__,
                })
            _send_json(exchange, 200, {"ok": True, "feeders": feeders})
        except Exception as e:
            traceback.print_exc()
            _send_error(exchange, 500, str(e))


class PackagesHandler(HttpHandler):
    def handle(self, exchange):
        try:
            packages = []
            for pkg in config.getPackages():
                packages.append({
                    "id": pkg.getId(),
                    "name": pkg.getName(),
                    "description": pkg.getDescription() if hasattr(pkg, "getDescription") else None,
                })
            _send_json(exchange, 200, {"ok": True, "packages": packages})
        except Exception as e:
            traceback.print_exc()
            _send_error(exchange, 500, str(e))


class CameraHandler(HttpHandler):
    def handle(self, exchange):
        try:
            # Parse query string for camera name
            uri = exchange.getRequestURI()
            query = uri.getQuery()  # e.g. "name=Bottom"
            camera_name = None
            if query:
                for param in query.split("&"):
                    if param.startswith("name="):
                        camera_name = param[5:]

            # Find the requested camera
            if camera_name:
                camera = None
                for head in machine.getHeads():
                    for cam in head.getCameras():
                        if cam.getName() == camera_name:
                            camera = cam
                            break
                    if camera:
                        break
                # Also check machine-level cameras (bottom cameras)
                if not camera:
                    for cam in machine.getCameras():
                        if cam.getName() == camera_name:
                            camera = cam
                            break
                if not camera:
                    _send_error(exchange, 404, "Camera '%s' not found" % camera_name)
                    return
            else:
                camera = machine.getDefaultHead().getDefaultCamera()

            # Capture frame
            image = camera.capture()
            baos = ByteArrayOutputStream()
            ImageIO.write(image, "PNG", baos)
            png_bytes = baos.toByteArray()

            exchange.getResponseHeaders().set("Content-Type", "image/png")
            exchange.sendResponseHeaders(200, len(png_bytes))
            out = exchange.getResponseBody()
            out.write(png_bytes)
            out.close()
        except Exception as e:
            traceback.print_exc()
            _send_error(exchange, 500, str(e))


class JobHandler(HttpHandler):
    def handle(self, exchange):
        try:
            job_processor = machine.getPnpJobProcessor()
            job = config.getJob() if hasattr(config, "getJob") else None

            result = {"ok": True, "job": None}

            if job:
                boards = []
                for board_location in job.getBoardLocations():
                    board = board_location.getBoard()
                    placements = []
                    for placement in board.getPlacements():
                        part = placement.getPart()
                        placements.append({
                            "id": placement.getId(),
                            "part_id": part.getId() if part else None,
                            "side": str(placement.getSide()),
                            "location": _get_location_dict(placement.getLocation()) if placement.getLocation() else None,
                            "type": str(placement.getType()),
                        })
                    boards.append({
                        "name": board.getName() if hasattr(board, "getName") else None,
                        "file": board.getFile().getPath() if board.getFile() else None,
                        "enabled": board_location.isEnabled(),
                        "location": _get_location_dict(board_location.getLocation()),
                        "placements": placements,
                    })
                result["job"] = {
                    "boards": boards,
                    "running": job_processor.isRunning() if job_processor else False,
                }

            _send_json(exchange, 200, result)
        except Exception as e:
            traceback.print_exc()
            _send_error(exchange, 500, str(e))


class ReloadHandler(HttpHandler):
    def handle(self, exchange):
        try:
            method = exchange.getRequestMethod()
            if method != "POST":
                _send_error(exchange, 405, "POST required")
                return
            # Reload configuration from disk
            config.load(config.getConfigurationDirectory())
            _send_json(exchange, 200, {"ok": True, "message": "Configuration reloaded"})
        except Exception as e:
            traceback.print_exc()
            _send_error(exchange, 500, str(e))


# ── Server startup ───────────────────────────────────────────

try:
    server = HttpServer.create(InetSocketAddress(BIND_IP, PORT), 0)
    server.createContext("/api/health", HealthHandler())
    server.createContext("/api/state", StateHandler())
    server.createContext("/api/parts", PartsHandler())
    server.createContext("/api/feeders", FeedersHandler())
    server.createContext("/api/packages", PackagesHandler())
    server.createContext("/api/camera", CameraHandler())
    server.createContext("/api/job", JobHandler())
    server.createContext("/api/reload", ReloadHandler())
    server.start()
    print("OpenPnP HTTP bridge listening on %s:%d" % (BIND_IP, PORT))
except Exception:
    print("Failed to start OpenPnP HTTP bridge:")
    traceback.print_exc()
