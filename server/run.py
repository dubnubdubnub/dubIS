"""Run the /v1 server: in-process daemon thread (desktop) or standalone."""

from __future__ import annotations

import threading

import uvicorn

from server.app import create_app


def start_server(
    api,
    host: str = "127.0.0.1",
    port: int = 7891,
    static_dir: str | None = None,
) -> "uvicorn.Server":
    config = uvicorn.Config(create_app(api, static_dir=static_dir), host=host, port=port,
                            log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="dubis-v1-server", daemon=True)
    thread.start()
    return server


def stop_server(server: "uvicorn.Server") -> None:
    server.should_exit = True
